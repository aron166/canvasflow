"""
CanvasFlow — execution backend.

A FastAPI service that takes a serialized node graph from the canvas and executes it,
routing every prompt through one of three interchangeable AI engines:

    A. anthropic_api  — official Anthropic SDK (pay-per-token developer key)
    B. claude_cli     — local, authenticated Claude Code CLI session (flat-rate subscription)
    C. ollama         — local Ollama daemon (free)

The engines share one interface (`Engine.generate`), so the graph executor never knows
or cares which one is active.

Run with:  uvicorn main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import uuid
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import ingest
import retrieval
from ingest import IngestError, ResolvedSource
from sources import SourceStore

load_dotenv()

# --------------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------------

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
DEFAULT_OLLAMA_MODEL = "llama3"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_TIMEOUT_SECONDS = 300
MAX_OUTPUT_TOKENS = 16_000

# model name -> its maximum context length, read once from Ollama's metadata
_OLLAMA_CONTEXT_CACHE: dict[str, int] = {}

# Node-type -> baseline system prompt. The node's own `persona` field is appended, so a
# user can specialize a CopywritingNode into "Luxury brand voice" without losing the
# structural instructions that make the node type useful in a pipeline.
NODE_SYSTEM_PROMPTS: dict[str, str] = {
    # Deliberately no sample marker values here: weaker models copy any literal example
    # straight into their answer as a fabricated citation.
    "sourceNode": (
        "You are a source reader. Answer strictly from the provided source material. "
        "The source has bracketed position markers at the start of lines — timestamps "
        "for transcripts, page numbers for documents. When you reference something "
        "specific, cite the marker that actually appears above that line, copied "
        "exactly. Never invent a marker, and never cite one you cannot see in the "
        "source. If the source does not contain the answer, say so plainly rather than "
        "filling the gap from general knowledge."
    ),
    "campaignInput": (
        "You are a marketing strategist. Take the raw campaign brief you are given and "
        "restate it as a tight, structured brief: audience, core promise, tone, "
        "constraints, and the single outcome that defines success. No preamble."
    ),
    "copywriting": (
        "You are a direct-response copywriter. Write copy that sounds like a person, not "
        "a brand deck. Lead with the outcome the reader cares about. Avoid generic "
        "AI-marketing phrasing, em-dash-heavy rhythm, and tricolon padding."
    ),
    "videoScript": (
        "You are a short-form video scriptwriter. Produce a shot-by-shot script with a "
        "hook in the first two seconds, spoken VO lines, and on-screen text cues. Mark "
        "timings. Keep spoken lines speakable out loud."
    ),
    "researchNode": (
        "You are a research analyst. Extract the claims, figures, and tensions in the "
        "input that would change what a marketer decides to do next. Say plainly when "
        "something is unsupported by the material you were given."
    ),
    "transformNode": (
        "You are a precise text transformer. Apply exactly the transformation requested "
        "to the input. Return only the transformed result — no commentary."
    ),
    "output": (
        "You are an editor. Assemble the inputs into the final deliverable requested. "
        "Preserve the substance; fix only what is broken."
    ),
}

GENERIC_SYSTEM_PROMPT = (
    "You are a component in a visual content-production workflow. Do the task you are "
    "given and return only the result."
)


# --------------------------------------------------------------------------------------
# Pydantic models — the wire contract shared with frontend/types.ts
# --------------------------------------------------------------------------------------


class EngineProvider(str, Enum):
    ANTHROPIC_API = "anthropic_api"
    CLAUDE_CLI = "claude_cli"
    OLLAMA = "ollama"


class EngineConfig(BaseModel):
    """Everything the backend needs to talk to the user's chosen engine.

    Credentials arrive per-request from the browser (LocalStorage) and are never
    persisted server-side. `ANTHROPIC_API_KEY` in the environment is a fallback only.
    """

    provider: EngineProvider = EngineProvider.OLLAMA

    # Option A
    anthropic_api_key: str | None = None
    anthropic_model: str = DEFAULT_ANTHROPIC_MODEL

    # Option B
    claude_cli_path: str = "claude"
    claude_session_token: str | None = Field(
        default=None,
        description=(
            "Optional sessionKey cookie from an authenticated claude.ai browser session. "
            "Passed to the CLI as CLAUDE_CODE_OAUTH_TOKEN when present; otherwise the "
            "CLI's own stored login is used."
        ),
    )

    # Option C
    ollama_url: str = DEFAULT_OLLAMA_URL
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    ollama_max_context_tokens: int = Field(
        default=32_768,
        description=(
            "Upper bound on the context window requested from Ollama. Ollama defaults to "
            "4096 and silently discards anything beyond it, so this is sized per request "
            "from the actual prompt. Raise for long sources, lower if the machine is "
            "short on RAM — a large window costs memory even when unused."
        ),
    )

    # Shared
    temperature: float | None = None
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS


class NodeData(BaseModel):
    """Payload carried by every canvas node. Unknown keys are kept so the frontend can
    evolve node fields without a backend deploy."""

    model_config = {"extra": "allow"}

    label: str = ""
    prompt: str = ""
    persona: str | None = None
    content: str | None = None  # static text the user typed into the node
    output: str | None = None  # last run's result, echoed back for convenience
    source_id: str | None = None  # set on source nodes; body is loaded at run time
    coverage: Literal["auto", "retrieve", "full"] = "auto"


class FlowNode(BaseModel):
    id: str
    type: str = "transformNode"
    data: NodeData = Field(default_factory=NodeData)
    position: dict[str, float] | None = None


class FlowEdge(BaseModel):
    id: str | None = None
    source: str
    target: str
    sourceHandle: str | None = None
    targetHandle: str | None = None


class ExecuteNodeRequest(BaseModel):
    """Run one node in isolation (the ▶ button on a node card)."""

    node: FlowNode
    upstream_outputs: list[str] = Field(default_factory=list)
    engine: EngineConfig


class ExecuteWorkflowRequest(BaseModel):
    """Run the whole graph."""

    nodes: list[FlowNode]
    edges: list[FlowEdge] = Field(default_factory=list)
    engine: EngineConfig


class RetrievalReport(BaseModel):
    """How one source's text reached the model — surfaced so an abridged context is
    visible on the node card rather than silently assumed complete."""

    source_id: str
    strategy: Literal["whole", "embeddings", "keyword", "full"]
    used_chars: int
    total_chars: int
    chunks_used: int
    chunks_total: int
    note: str


class NodeResult(BaseModel):
    node_id: str
    status: Literal["ok", "error", "skipped"]
    output: str = ""
    error: str | None = None
    provider: EngineProvider | None = None
    model: str | None = None
    duration_ms: int = 0
    retrieval: list[RetrievalReport] | None = None


class WorkflowResponse(BaseModel):
    status: Literal["ok", "partial", "error"]
    results: list[NodeResult]
    order: list[str]
    duration_ms: int


class EngineStatus(BaseModel):
    provider: EngineProvider
    available: bool
    detail: str


# --------------------------------------------------------------------------------------
# Engines
# --------------------------------------------------------------------------------------


class EngineError(RuntimeError):
    """Anything that stops one node from producing output. Carries a message meant to be
    shown directly on the node card, so it must be actionable."""


class Engine(ABC):
    provider: EngineProvider

    def __init__(self, config: EngineConfig) -> None:
        self.config = config

    @property
    @abstractmethod
    def model_name(self) -> str: ...

    @abstractmethod
    async def generate(self, system: str, prompt: str) -> str: ...

    @abstractmethod
    async def probe(self) -> EngineStatus:
        """Cheap reachability check for the Settings panel — must not spend tokens."""


# ---------------------------------------------------------------- Option A: Anthropic API


class AnthropicEngine(Engine):
    provider = EngineProvider.ANTHROPIC_API

    @property
    def model_name(self) -> str:
        return self.config.anthropic_model or DEFAULT_ANTHROPIC_MODEL

    def _client(self):
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover - install-time failure
            raise EngineError(
                "The `anthropic` package is not installed. Run: pip install anthropic"
            ) from exc

        key = self.config.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise EngineError(
                "No Anthropic API key. Add one in Settings, or set ANTHROPIC_API_KEY in "
                "backend/.env."
            )
        return AsyncAnthropic(api_key=key, timeout=float(self.config.timeout_seconds))

    async def generate(self, system: str, prompt: str) -> str:
        import anthropic

        client = self._client()
        try:
            # Streaming even though we only want the final text: it keeps a long
            # generation from tripping the SDK's non-streaming HTTP timeout.
            async with client.messages.stream(
                model=self.model_name,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                message = await stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            raise EngineError("Anthropic rejected the API key (401).") from exc
        except anthropic.PermissionDeniedError as exc:
            raise EngineError(
                f"API key lacks access to {self.model_name} (403)."
            ) from exc
        except anthropic.NotFoundError as exc:
            raise EngineError(
                f"Unknown model '{self.model_name}' (404). Check the model ID in Settings."
            ) from exc
        except anthropic.RateLimitError as exc:
            retry = exc.response.headers.get("retry-after", "60")
            raise EngineError(f"Rate limited by Anthropic. Retry in {retry}s.") from exc
        except anthropic.APIStatusError as exc:
            raise EngineError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise EngineError("Could not reach the Anthropic API — check connectivity.") from exc
        finally:
            await client.close()

        if message.stop_reason == "refusal":
            raise EngineError("Claude declined this request for safety reasons.")

        text = "".join(b.text for b in message.content if b.type == "text").strip()
        if not text:
            raise EngineError("Anthropic returned an empty response.")
        return text

    async def probe(self) -> EngineStatus:
        key = self.config.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
        if not key:
            return EngineStatus(
                provider=self.provider, available=False, detail="No API key configured."
            )
        try:
            client = self._client()
            try:
                await client.models.retrieve(self.model_name)
            finally:
                await client.close()
        except EngineError as exc:
            return EngineStatus(provider=self.provider, available=False, detail=str(exc))
        except Exception as exc:  # noqa: BLE001 - probe must never raise
            return EngineStatus(
                provider=self.provider, available=False, detail=f"{type(exc).__name__}: {exc}"
            )
        return EngineStatus(
            provider=self.provider, available=True, detail=f"Key valid · {self.model_name}"
        )


# ------------------------------------------------------- Option B: Claude subscription CLI


class ClaudeCLIEngine(Engine):
    """Bridges to a flat-rate Claude subscription by piping prompts through an already
    authenticated Claude Code CLI session.

    The CLI holds the OAuth session on disk after a one-time `claude` login, so no API
    key is involved and usage bills against the subscription rather than per token.
    A `claude_session_token` may be supplied instead, in which case it is injected as
    CLAUDE_CODE_OAUTH_TOKEN for the child process only.
    """

    provider = EngineProvider.CLAUDE_CLI

    @property
    def model_name(self) -> str:
        return "claude-code-cli"

    def _resolve_binary(self) -> str:
        candidate = self.config.claude_cli_path or "claude"
        resolved = shutil.which(candidate) or (
            candidate if os.path.isfile(candidate) and os.access(candidate, os.X_OK) else None
        )
        if not resolved:
            raise EngineError(
                f"Claude CLI not found at '{candidate}'. Install it with "
                "`npm install -g @anthropic-ai/claude-code`, then log in once by running "
                "`claude`. Set the full binary path in Settings if it lives outside PATH."
            )
        return resolved

    def _child_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.config.claude_session_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = self.config.claude_session_token
        # An API key in the environment would shadow the subscription session and start
        # billing per token — exactly what this engine exists to avoid.
        env.pop("ANTHROPIC_API_KEY", None)
        env["CLAUDE_NONINTERACTIVE"] = "1"
        return env

    async def generate(self, system: str, prompt: str) -> str:
        binary = self._resolve_binary()
        # `-p` is non-interactive print mode. The prompt goes over stdin so it is not
        # bounded by ARG_MAX and never lands in the process table.
        argv = [binary, "-p", "--output-format", "text", "--append-system-prompt", system]

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._child_env(),
            )
        except OSError as exc:
            raise EngineError(f"Could not start the Claude CLI: {exc}") from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")),
                timeout=self.config.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise EngineError(
                f"Claude CLI timed out after {self.config.timeout_seconds}s."
            ) from exc

        err = stderr.decode("utf-8", errors="replace").strip()
        out = stdout.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            hint = ""
            lowered = err.lower()
            if "login" in lowered or "auth" in lowered or "unauthor" in lowered:
                hint = " — run `claude` in a terminal on the backend host and log in."
            raise EngineError(
                f"Claude CLI exited {proc.returncode}: {err or 'no stderr output'}{hint}"
            )

        if not out:
            raise EngineError(
                "Claude CLI produced no output. Verify `claude -p \"hi\"` works on the "
                "backend host."
            )
        return out

    async def probe(self) -> EngineStatus:
        try:
            binary = self._resolve_binary()
        except EngineError as exc:
            return EngineStatus(provider=self.provider, available=False, detail=str(exc))

        try:
            proc = await asyncio.create_subprocess_exec(
                binary,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._child_env(),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
        except (OSError, asyncio.TimeoutError) as exc:
            return EngineStatus(
                provider=self.provider, available=False, detail=f"CLI did not respond: {exc}"
            )

        if proc.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip() or "non-zero exit"
            return EngineStatus(provider=self.provider, available=False, detail=detail)

        version = stdout.decode("utf-8", errors="replace").strip()
        return EngineStatus(
            provider=self.provider, available=True, detail=f"{version} · session login"
        )


# ------------------------------------------------------------------- Option C: Local Ollama


class OllamaEngine(Engine):
    provider = EngineProvider.OLLAMA

    @property
    def model_name(self) -> str:
        return self.config.ollama_model or DEFAULT_OLLAMA_MODEL

    @property
    def base_url(self) -> str:
        return (self.config.ollama_url or DEFAULT_OLLAMA_URL).rstrip("/")

    async def _model_context_limit(self) -> int:
        """The model's own maximum context, from Ollama's metadata.

        Cached per model: it is a fixed property of the weights, and /api/show is a
        round trip we would otherwise pay on every single node execution.
        """
        cached = _OLLAMA_CONTEXT_CACHE.get(self.model_name)
        if cached is not None:
            return cached

        limit = 8_192  # a safe floor if the metadata doesn't say
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    f"{self.base_url}/api/show", json={"model": self.model_name}
                )
                if response.status_code == 200:
                    info = response.json().get("model_info", {}) or {}
                    for key, value in info.items():
                        if key.endswith(".context_length") and isinstance(value, int):
                            limit = value
                            break
        except Exception:  # noqa: BLE001 - fall back to the floor
            pass

        _OLLAMA_CONTEXT_CACHE[self.model_name] = limit
        return limit

    async def _num_ctx_for(self, text: str) -> int:
        """Sizes the context window to the prompt.

        This is the difference between the model reading the whole source and reading a
        fragment of it: Ollama's default window is 4096 tokens and it discards the
        overflow *without any error* — the reply just quietly answers from part of the
        input.
        """
        # ~3.5 chars/token is conservative for English; over-estimating costs a little
        # RAM, under-estimating silently loses content, so bias upward.
        needed = int(len(text) / 3.5) + MAX_OUTPUT_TOKENS // 8
        ceiling = min(await self._model_context_limit(), self.config.ollama_max_context_tokens)
        return max(4_096, min(needed, ceiling))

    async def generate(self, system: str, prompt: str) -> str:
        options: dict[str, Any] = {"num_ctx": await self._num_ctx_for(system + prompt)}
        if self.config.temperature is not None:
            options["temperature"] = self.config.temperature

        payload: dict[str, Any] = {
            "model": self.model_name,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "options": options,
        }

        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                response = await client.post(f"{self.base_url}/api/generate", json=payload)
        except httpx.ConnectError as exc:
            raise EngineError(
                f"Ollama is not reachable at {self.base_url}. Start it with `ollama serve`."
            ) from exc
        except httpx.TimeoutException as exc:
            raise EngineError(
                f"Ollama timed out after {self.config.timeout_seconds}s — the model may "
                "still be loading into memory."
            ) from exc

        if response.status_code == 404:
            raise EngineError(
                f"Ollama has no model '{self.model_name}'. Pull it: "
                f"`ollama pull {self.model_name}`."
            )
        if response.status_code >= 400:
            raise EngineError(f"Ollama error {response.status_code}: {response.text[:300]}")

        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise EngineError("Ollama returned a non-JSON response.") from exc

        text = (body.get("response") or "").strip()
        if not text:
            raise EngineError("Ollama returned an empty response.")
        return text

    async def probe(self) -> EngineStatus:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                tags = response.json().get("models", [])
        except Exception as exc:  # noqa: BLE001 - probe must never raise
            return EngineStatus(
                provider=self.provider,
                available=False,
                detail=f"Not reachable at {self.base_url} ({type(exc).__name__}).",
            )

        names = [m.get("name", "") for m in tags]
        installed = any(n == self.model_name or n.startswith(f"{self.model_name}:") for n in names)
        if not installed:
            return EngineStatus(
                provider=self.provider,
                available=False,
                detail=f"Running, but '{self.model_name}' is not pulled. Have: {', '.join(names) or 'none'}",
            )
        return EngineStatus(
            provider=self.provider, available=True, detail=f"{len(names)} model(s) · {self.model_name}"
        )


ENGINE_REGISTRY: dict[EngineProvider, type[Engine]] = {
    EngineProvider.ANTHROPIC_API: AnthropicEngine,
    EngineProvider.CLAUDE_CLI: ClaudeCLIEngine,
    EngineProvider.OLLAMA: OllamaEngine,
}


def build_engine(config: EngineConfig) -> Engine:
    engine_cls = ENGINE_REGISTRY.get(config.provider)
    if engine_cls is None:  # unreachable while the enum and registry agree
        raise EngineError(f"Unknown engine provider '{config.provider}'.")
    return engine_cls(config)


# --------------------------------------------------------------------------------------
# Prompt assembly + graph execution
# --------------------------------------------------------------------------------------


def build_system_prompt(node: FlowNode) -> str:
    base = NODE_SYSTEM_PROMPTS.get(node.type, GENERIC_SYSTEM_PROMPT)
    persona = (node.data.persona or "").strip()
    return f"{base}\n\nAdopt this persona and voice: {persona}" if persona else base


async def engine_context_budget(engine: Engine) -> int:
    """Characters of source material this engine can actually take in one call.

    For Ollama this is derived from the model's real context length rather than a fixed
    guess — an 8k model and a 128k model should not be handed the same amount.
    """
    if isinstance(engine, OllamaEngine):
        limit = min(
            await engine._model_context_limit(),  # noqa: SLF001 - same module
            engine.config.ollama_max_context_tokens,
        )
        # Leave room for the system prompt, the instruction, and the reply.
        usable_tokens = max(2_048, limit - MAX_OUTPUT_TOKENS // 8 - 1_024)
        return int(usable_tokens * 3.5)
    return retrieval.context_budget(engine.provider.value)


async def build_source_section(
    node: FlowNode, store: SourceStore, engine: Engine, budget_chars: int
) -> tuple[str, RetrievalReport | None]:
    """Loads this node's ingested source and fits it to the engine's context budget.

    Returns the prompt section and a report of how it was assembled — the report is what
    tells the user whether they got the whole source or a retrieved subset.
    """
    source_id = (node.data.source_id or "").strip()
    if not source_id:
        return "", None

    record = store.get(source_id)
    text = store.text(source_id)
    if record is None or text is None:
        raise EngineError(
            "This source is no longer available on the backend — re-ingest it on the node."
        )

    outcome = await retrieval.assemble_context(
        source_id=source_id,
        title=record["title"],
        # The downstream instruction is the retrieval query: it is what the selected
        # passages actually need to be relevant to.
        text=text,
        query=(node.data.prompt or "").strip() or record["title"],
        budget_chars=budget_chars,
        ollama_url=engine.config.ollama_url,
    )

    header = f'<source title="{record["title"]}" origin="{record["origin"]}">'
    section = f"{header}\n{outcome.text}\n</source>"

    report = RetrievalReport(
        source_id=source_id,
        strategy=outcome.strategy,  # type: ignore[arg-type]
        used_chars=outcome.used_chars,
        total_chars=outcome.total_chars,
        chunks_used=outcome.chunks_used,
        chunks_total=outcome.chunks_total,
        note=outcome.note,
    )
    return section, report


def build_user_prompt(
    node: FlowNode, upstream_outputs: list[str], source_section: str = ""
) -> str:
    sections: list[str] = []

    # Source material leads: everything downstream is reasoning about it.
    if source_section:
        sections.append(source_section)

    upstream = [text.strip() for text in upstream_outputs if text and text.strip()]
    if upstream:
        joined = "\n\n---\n\n".join(upstream)
        sections.append(f"<upstream_context>\n{joined}\n</upstream_context>")

    content = (node.data.content or "").strip()
    if content:
        sections.append(f"<node_content>\n{content}\n</node_content>")

    instruction = (node.data.prompt or "").strip()
    if instruction:
        sections.append(f"<task>\n{instruction}\n</task>")

    if not sections:
        raise EngineError(
            "Nothing to run: this node has no prompt, no content, no source, and no "
            "connected input."
        )
    return "\n\n".join(sections)


def topological_order(nodes: list[FlowNode], edges: list[FlowEdge]) -> list[list[str]]:
    """Group node IDs into dependency levels. Nodes in the same level are independent and
    execute concurrently. Raises on a cycle — a cyclic canvas has no correct run order."""

    ids = {n.id for n in nodes}
    indegree = {node_id: 0 for node_id in ids}
    children: dict[str, list[str]] = {node_id: [] for node_id in ids}

    for edge in edges:
        # Edges pointing at deleted nodes are normal mid-edit; ignore rather than fail.
        if edge.source not in ids or edge.target not in ids:
            continue
        children[edge.source].append(edge.target)
        indegree[edge.target] += 1

    levels: list[list[str]] = []
    frontier = sorted(node_id for node_id, deg in indegree.items() if deg == 0)
    seen = 0

    while frontier:
        levels.append(frontier)
        seen += len(frontier)
        next_frontier: list[str] = []
        for node_id in frontier:
            for child in children[node_id]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    next_frontier.append(child)
        frontier = sorted(next_frontier)

    if seen != len(ids):
        stuck = sorted(node_id for node_id, deg in indegree.items() if deg > 0)
        raise EngineError(f"The graph has a cycle involving: {', '.join(stuck)}")

    return levels


_EMPTY_SENTINEL = "NOTHING RELEVANT"


def _is_empty_finding(result: str) -> bool:
    """True when a pass genuinely found nothing.

    The sentinel phrase also appears in the instruction we send, and small models echo
    their instructions — so a substring match discards real findings. A pass only counts
    as empty when the sentinel is essentially the *entire* reply.
    """
    stripped = result.strip()
    if not stripped:
        return True
    if _EMPTY_SENTINEL not in stripped.upper():
        return False
    # Everything except the sentinel and punctuation — if little remains, it's a no-op.
    remainder = re.sub(
        _EMPTY_SENTINEL, "", stripped, flags=re.IGNORECASE
    ).strip(" .,:;!-—\"'\n\t")
    return len(remainder) < 40


async def run_full_coverage(
    engine: Engine,
    node: FlowNode,
    upstream_outputs: list[str],
    text: str,
    title: str,
    budget: int,
) -> tuple[str, RetrievalReport]:
    """Reads the entire source in sequential passes, then synthesises one answer.

    Retrieval answers "what in this source is relevant to my question"; this answers
    "what does the whole source say", which is the only correct mode for tasks like
    summarising an hour-long video or auditing a long document for every occurrence of
    something. It costs one model call per window plus one to combine.
    """
    windows = retrieval.plan_windows(text, budget, node.data.source_id or "w")
    instruction = (node.data.prompt or "").strip() or f"Summarise this part of {title}."
    system = build_system_prompt(node)

    async def read_window(index: int, window: str) -> str:
        pass_prompt = (
            f'<source title="{title}" part="{index + 1} of {len(windows)}">\n{window}\n</source>\n\n'
            f"<task>\nThis is part {index + 1} of {len(windows)} of a longer source. "
            f"Extract everything in THIS PART that is relevant to the following "
            f"instruction, keeping the bracketed position markers on anything you quote. "
            f"If this part contains nothing relevant, reply exactly: NOTHING RELEVANT.\n\n"
            f"{instruction}\n</task>"
        )
        return await engine.generate(system, pass_prompt)

    # Sequential rather than concurrent: a local model serves one request at a time
    # anyway, and firing N at once just queues them while multiplying peak RAM.
    findings: list[str] = []
    empty_passes = 0
    for index, window in enumerate(windows):
        try:
            result = (await read_window(index, window)).strip()
        except EngineError as exc:
            result = f"(part {index + 1} failed: {exc})"

        if _is_empty_finding(result):
            empty_passes += 1
            continue
        findings.append(f"--- from part {index + 1} of {len(windows)} ---\n{result}")

    if not findings:
        raise EngineError(
            f"Read all {len(windows)} parts of the source, but the model reported nothing "
            f"relevant in any of them. Either the source genuinely doesn't cover this, or "
            f"the instruction is too narrow — try rephrasing it, or switch this node to "
            f"Relevant coverage."
        )

    combined = "\n\n".join(findings)
    # The reduce step can itself overflow on a very long source; fall back to retrieval
    # over the findings rather than silently truncating them.
    if len(combined) > budget:
        outcome = await retrieval.assemble_context(
            source_id=f"{node.data.source_id}:findings",
            title=title,
            text=combined,
            query=instruction,
            budget_chars=budget,
            ollama_url=engine.config.ollama_url,
        )
        combined = outcome.text

    reduce_prompt = build_user_prompt(
        node,
        upstream_outputs,
        f"<findings source=\"{title}\" parts=\"{len(windows)}\">\n{combined}\n</findings>",
    )
    output = await engine.generate(
        system,
        f"{reduce_prompt}\n\n<note>The findings above were extracted from every part of "
        f"the source in order. Synthesise them into a single answer to the task. Do not "
        f"mention the part numbers in your answer.</note>",
    )

    report = RetrievalReport(
        source_id=node.data.source_id or "",
        strategy="full",
        used_chars=len(text),
        total_chars=len(text),
        chunks_used=len(windows),
        chunks_total=len(windows),
        note=(
            f"Full coverage: read all {len(text):,} chars in {len(windows)} sequential "
            f"passes, then synthesised. Nothing skipped."
        ),
    )
    return output, report


async def run_node(
    engine: Engine, node: FlowNode, upstream_outputs: list[str], store: SourceStore
) -> NodeResult:
    started = time.monotonic()
    reports: list[RetrievalReport] = []

    try:
        system = build_system_prompt(node)
        budget = await engine_context_budget(engine)
        source_id = (node.data.source_id or "").strip()
        source_text = store.text(source_id) if source_id else None

        # Full coverage only earns its extra calls when the source genuinely overflows.
        wants_full = node.data.coverage == "full"
        if wants_full and source_text and len(source_text) > budget:
            record = store.get(source_id)
            output, report = await run_full_coverage(
                engine,
                node,
                upstream_outputs,
                source_text,
                record["title"] if record else "source",
                budget,
            )
            return NodeResult(
                node_id=node.id,
                status="ok",
                output=output,
                provider=engine.provider,
                model=engine.model_name,
                duration_ms=int((time.monotonic() - started) * 1000),
                retrieval=[report],
            )

        section, report = await build_source_section(node, store, engine, budget)
        if report is not None:
            reports.append(report)
        prompt = build_user_prompt(node, upstream_outputs, section)
        output = await engine.generate(system, prompt)
        status: Literal["ok", "error"] = "ok"
        error = None
    except EngineError as exc:
        output, error, status = "", str(exc), "error"
    except Exception as exc:  # noqa: BLE001 - one bad node must not kill the run
        output, error, status = "", f"Unexpected {type(exc).__name__}: {exc}", "error"

    return NodeResult(
        node_id=node.id,
        status=status,
        output=output,
        error=error,
        provider=engine.provider,
        model=engine.model_name,
        duration_ms=int((time.monotonic() - started) * 1000),
        retrieval=reports or None,
    )


# --------------------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------------------

app = FastAPI(
    title="CanvasFlow Engine",
    version="0.1.0",
    description="Graph execution service with a swappable 3-way AI backend.",
)

# Any localhost port, not just 3000. The frontend moves to 3001+ whenever another
# project holds 3000, and a hardcoded origin turns that into a CORS rejection that the
# browser reports to the app as an unreachable server — a misleading symptom for what is
# really a config mismatch.
#
# Loopback only: this still refuses every remote origin. Set CANVASFLOW_ALLOWED_ORIGINS
# to an explicit comma-separated list when deploying anywhere real.
LOCALHOST_ORIGIN_PATTERN = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"

_explicit_origins = os.getenv("CANVASFLOW_ALLOWED_ORIGINS", "").strip()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _explicit_origins.split(",") if o.strip()],
    allow_origin_regex=None if _explicit_origins else LOCALHOST_ORIGIN_PATTERN,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


STORE = SourceStore()

# Ingestion jobs, in-process. A transcription can run for minutes, so the HTTP request
# that starts it must not be the thing that waits for it.
#
# ponytail: a plain dict, not Celery. Celery buys durability across restarts and
# multi-machine workers; this is a single-user local tool where a restart means the
# canvas is reloaded anyway. Swap in a broker only if CanvasFlow ever becomes hosted.
INGEST_JOBS: dict[str, dict[str, Any]] = {}
INGEST_SEMAPHORE = asyncio.Semaphore(2)  # transcription is CPU-bound; don't thrash


class IngestUrlRequest(BaseModel):
    url: str


class IngestJob(BaseModel):
    job_id: str
    state: Literal["queued", "running", "done", "error"]
    stage: str = ""
    source: dict[str, Any] | None = None
    error: str | None = None


async def _run_ingest_job(job_id: str, coroutine_factory: Any, label: str) -> None:
    """Drives one ingestion to completion, recording progress for the poller."""
    INGEST_JOBS[job_id].update(state="running", stage=f"Processing {label}…")
    async with INGEST_SEMAPHORE:
        try:
            resolved: ResolvedSource = await coroutine_factory()
            public = STORE.save(resolved)
            INGEST_JOBS[job_id].update(state="done", stage="Complete", source=public)
        except IngestError as exc:
            INGEST_JOBS[job_id].update(state="error", error=str(exc))
        except Exception as exc:  # noqa: BLE001 - a job must never take the server down
            INGEST_JOBS[job_id].update(
                state="error", error=f"Unexpected {type(exc).__name__}: {exc}"
            )


def _start_job(coroutine_factory: Any, label: str) -> IngestJob:
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    INGEST_JOBS[job_id] = {"job_id": job_id, "state": "queued", "stage": "Queued"}
    asyncio.create_task(_run_ingest_job(job_id, coroutine_factory, label))
    return IngestJob(**INGEST_JOBS[job_id])


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "canvasflow-engine"}


@app.post("/api/engine/status", response_model=EngineStatus)
async def engine_status(config: EngineConfig) -> EngineStatus:
    """Used by the Settings panel to show a live green/red dot per engine."""
    try:
        return await build_engine(config).probe()
    except EngineError as exc:
        return EngineStatus(provider=config.provider, available=False, detail=str(exc))


@app.post("/api/sources/ingest")
async def ingest_url(request: IngestUrlRequest) -> Any:
    """Ingests a URL.

    YouTube transcripts and web pages resolve in seconds, so they return the finished
    source directly — the common case stays a single round trip. Anything that has to
    download or transcribe returns a job to poll instead.
    """
    url = request.url.strip()
    if not url:
        return JSONResponse(status_code=400, content={"detail": "No URL provided."})

    normalized = url if url.startswith(("http://", "https://")) else f"https://{url}"
    kind = ingest.classify_url(normalized)

    if kind in (ingest.SourceKind.MEDIA, ingest.SourceKind.DOCUMENT):
        job = _start_job(lambda: ingest.resolve_url(normalized), kind.value)
        return JSONResponse(status_code=202, content=job.model_dump())

    try:
        resolved = await ingest.resolve_url(normalized)
    except IngestError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    return STORE.save(resolved)


@app.post("/api/sources/upload")
async def upload_source(file: UploadFile = File(...)) -> Any:
    """Accepts a file upload. Media is transcribed in the background; documents parse
    fast enough to return inline."""
    import tempfile

    filename = os.path.basename(file.filename or "upload")
    suffix = os.path.splitext(filename)[1].lower()

    written = 0
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > ingest.MAX_DOWNLOAD_BYTES:
                    handle.close()
                    os.unlink(handle.name)
                    return JSONResponse(
                        status_code=413,
                        content={
                            "detail": f"{filename} exceeds the "
                            f"{ingest.MAX_DOWNLOAD_BYTES // 1024 // 1024} MB limit."
                        },
                    )
                handle.write(chunk)
            temp_path = handle.name
    finally:
        await file.close()

    media_suffixes = {".mp3", ".mp4", ".wav", ".m4a", ".mov", ".webm", ".mkv", ".ogg", ".flac"}

    if suffix in media_suffixes:
        # The temp file must outlive the request, so the job owns cleanup.
        async def transcribe() -> ResolvedSource:
            try:
                return await ingest.resolve_file(temp_path, filename)
            finally:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

        job = _start_job(transcribe, filename)
        return JSONResponse(status_code=202, content=job.model_dump())

    try:
        resolved = await ingest.resolve_file(temp_path, filename)
    except IngestError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    return STORE.save(resolved)


@app.get("/api/sources/jobs/{job_id}", response_model=IngestJob)
async def ingest_job_status(job_id: str) -> Any:
    job = INGEST_JOBS.get(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"detail": "Unknown job."})
    return IngestJob(**job)


@app.get("/api/sources")
async def list_sources() -> list[dict[str, Any]]:
    return STORE.list_all()


@app.delete("/api/sources/{source_id}")
async def delete_source(source_id: str) -> Any:
    if not STORE.delete(source_id):
        return JSONResponse(status_code=404, content={"detail": "Unknown source."})
    return {"status": "deleted", "source_id": source_id}


@app.post("/api/execute/node", response_model=NodeResult)
async def execute_node(request: ExecuteNodeRequest) -> NodeResult:
    try:
        engine = build_engine(request.engine)
    except EngineError as exc:
        return NodeResult(node_id=request.node.id, status="error", error=str(exc))
    return await run_node(engine, request.node, request.upstream_outputs, STORE)


@app.post("/api/execute/workflow", response_model=WorkflowResponse)
async def execute_workflow(request: ExecuteWorkflowRequest) -> Any:
    started = time.monotonic()

    if not request.nodes:
        return WorkflowResponse(status="ok", results=[], order=[], duration_ms=0)

    try:
        engine = build_engine(request.engine)
        levels = topological_order(request.nodes, request.edges)
    except EngineError as exc:
        return JSONResponse(status_code=400, content={"status": "error", "detail": str(exc)})

    by_id = {node.id: node for node in request.nodes}
    parents: dict[str, list[str]] = {node.id: [] for node in request.nodes}
    for edge in request.edges:
        if edge.source in by_id and edge.target in by_id:
            parents[edge.target].append(edge.source)

    results: dict[str, NodeResult] = {}
    order: list[str] = []

    for level in levels:
        runnable: list[FlowNode] = []
        for node_id in level:
            order.append(node_id)
            # A node whose upstream failed cannot receive the context it was drawn to
            # receive — running it anyway would produce confidently wrong output.
            broken = [
                p for p in parents[node_id] if results.get(p) and results[p].status != "ok"
            ]
            if broken:
                results[node_id] = NodeResult(
                    node_id=node_id,
                    status="skipped",
                    error=f"Upstream node(s) did not produce output: {', '.join(broken)}",
                )
            else:
                runnable.append(by_id[node_id])

        if not runnable:
            continue

        level_results = await asyncio.gather(
            *(
                run_node(
                    engine,
                    node,
                    [
                        results[p].output
                        for p in parents[node.id]
                        if p in results and results[p].status == "ok"
                    ],
                    STORE,
                )
                for node in runnable
            )
        )
        for result in level_results:
            results[result.node_id] = result

    ordered = [results[node_id] for node_id in order]
    if all(r.status == "ok" for r in ordered):
        status: Literal["ok", "partial", "error"] = "ok"
    elif any(r.status == "ok" for r in ordered):
        status = "partial"
    else:
        status = "error"

    return WorkflowResponse(
        status=status,
        results=ordered,
        order=order,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


# --------------------------------------------------------------------------------------
# Self-check:  python main.py
# Covers the two pieces of non-trivial pure logic — graph ordering and prompt assembly.
# --------------------------------------------------------------------------------------


def _demo() -> None:
    def node(node_id: str, **data: Any) -> FlowNode:
        return FlowNode(id=node_id, type="transformNode", data=NodeData(**data))

    # Diamond: a -> b, a -> c, b -> d, c -> d
    nodes = [node(i, prompt="x") for i in "abcd"]
    edges = [
        FlowEdge(source="a", target="b"),
        FlowEdge(source="a", target="c"),
        FlowEdge(source="b", target="d"),
        FlowEdge(source="c", target="d"),
    ]
    assert topological_order(nodes, edges) == [["a"], ["b", "c"], ["d"]]

    # Edges to deleted nodes are ignored, not fatal.
    assert topological_order([node("a", prompt="x")], [FlowEdge(source="a", target="ghost")]) == [["a"]]

    # Cycles are rejected.
    try:
        topological_order(
            [node("a", prompt="x"), node("b", prompt="x")],
            [FlowEdge(source="a", target="b"), FlowEdge(source="b", target="a")],
        )
        raise AssertionError("expected a cycle to raise")
    except EngineError as exc:
        assert "cycle" in str(exc)

    # Prompt assembly keeps every populated section, and only those.
    prompt = build_user_prompt(node("a", prompt="Rewrite it.", content="Draft"), ["Upstream"])
    assert "<upstream_context>" in prompt and "Upstream" in prompt
    assert "<node_content>" in prompt and "Draft" in prompt
    assert "<task>" in prompt and "Rewrite it." in prompt

    assert "<upstream_context>" not in build_user_prompt(node("a", prompt="Go"), [])
    assert "<node_content>" not in build_user_prompt(node("a", prompt="Go"), [])

    # An empty node is a user error, surfaced as one.
    try:
        build_user_prompt(node("a"), ["  "])
        raise AssertionError("expected an empty node to raise")
    except EngineError:
        pass

    # Empty-pass detection: the sentinel appears in our own instruction, so only a reply
    # that is essentially just the sentinel counts as empty. Anything substantive is a
    # real finding even when it quotes the phrase back.
    assert _is_empty_finding("")
    assert _is_empty_finding("   ")
    assert _is_empty_finding("NOTHING RELEVANT")
    assert _is_empty_finding("nothing relevant.")
    assert _is_empty_finding("  NOTHING RELEVANT  \n")
    assert not _is_empty_finding(
        "If this part contains nothing relevant, reply NOTHING RELEVANT. "
        "This part discusses prompt injection at [51:20] and data poisoning at [54:02]."
    )
    assert not _is_empty_finding("[12:00] The speaker introduces the tokenizer.")

    # Persona is layered onto the node-type system prompt, not swapped for it.
    styled = build_system_prompt(FlowNode(id="a", type="copywriting", data=NodeData(persona="Wry")))
    assert "direct-response copywriter" in styled and "Wry" in styled

    print("self-check passed")


if __name__ == "__main__":
    _demo()
