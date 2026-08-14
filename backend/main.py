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
import shutil
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

load_dotenv()

# --------------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------------

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
DEFAULT_OLLAMA_MODEL = "llama3"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_TIMEOUT_SECONDS = 300
MAX_OUTPUT_TOKENS = 16_000

# Node-type -> baseline system prompt. The node's own `persona` field is appended, so a
# user can specialize a CopywritingNode into "Luxury brand voice" without losing the
# structural instructions that make the node type useful in a pipeline.
NODE_SYSTEM_PROMPTS: dict[str, str] = {
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


class NodeResult(BaseModel):
    node_id: str
    status: Literal["ok", "error", "skipped"]
    output: str = ""
    error: str | None = None
    provider: EngineProvider | None = None
    model: str | None = None
    duration_ms: int = 0


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

    async def generate(self, system: str, prompt: str) -> str:
        payload: dict[str, Any] = {
            "model": self.model_name,
            "prompt": prompt,
            "system": system,
            "stream": False,
        }
        if self.config.temperature is not None:
            payload["options"] = {"temperature": self.config.temperature}

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


def build_user_prompt(node: FlowNode, upstream_outputs: list[str]) -> str:
    sections: list[str] = []

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
            "Nothing to run: this node has no prompt, no content, and no connected input."
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


async def run_node(
    engine: Engine, node: FlowNode, upstream_outputs: list[str]
) -> NodeResult:
    started = time.monotonic()
    try:
        system = build_system_prompt(node)
        prompt = build_user_prompt(node, upstream_outputs)
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
    )


# --------------------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------------------

app = FastAPI(
    title="CanvasFlow Engine",
    version="0.1.0",
    description="Graph execution service with a swappable 3-way AI backend.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CANVASFLOW_ALLOWED_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.post("/api/execute/node", response_model=NodeResult)
async def execute_node(request: ExecuteNodeRequest) -> NodeResult:
    try:
        engine = build_engine(request.engine)
    except EngineError as exc:
        return NodeResult(node_id=request.node.id, status="error", error=str(exc))
    return await run_node(engine, request.node, request.upstream_outputs)


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

    # Persona is layered onto the node-type system prompt, not swapped for it.
    styled = build_system_prompt(FlowNode(id="a", type="copywriting", data=NodeData(persona="Wry")))
    assert "direct-response copywriter" in styled and "Wry" in styled

    print("self-check passed")


if __name__ == "__main__":
    _demo()
