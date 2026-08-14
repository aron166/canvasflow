# CanvasFlow

An infinite, node-based visual workspace for content creation, multi-agent marketing
workflows, and programmatic scripting. Open-source alternative to Poppy AI.

**Status:** foundation — canvas, node types, 3-way AI engine, workflow execution.

## The 3-Way AI Engine

Every node execution is routed through one of three engines, chosen in Settings:

| Option | Engine | What it costs | How it works |
| --- | --- | --- | --- |
| **A** | Anthropic API | Pay per token | Official `anthropic` SDK, `claude-opus-5` by default |
| **B** | Claude Subscription Bridge | Your flat-rate plan | Pipes prompts through an authenticated `claude` CLI session via `subprocess` |
| **C** | Local Ollama | Free | Async HTTPX to `http://localhost:11434/api/generate` (Llama 3 / Mistral) |

## Quickstart

```bash
./dev.sh
```

That's it. It creates the Python venv and installs both dependency sets on first run,
then starts the API and the canvas together and prints the URLs. If port 3000 or 8000 is
taken by another project, it moves to the next free one and tells you.

<details>
<summary>Starting the two halves manually</summary>

```bash
# 1. Backend
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env                          # optional: set ANTHROPIC_API_KEY
.venv/bin/uvicorn main:app --reload --port 8000

# 2. Frontend (new terminal)
cd frontend
npm install
npx next dev -p 3000
```

**Use `.venv/bin/uvicorn`, not plain `uvicorn`.** If a system-wide uvicorn is installed,
a bare `uvicorn main:app` runs under the system Python, which cannot see anything in the
venv — it fails with `ModuleNotFoundError: No module named 'dotenv'` even though the
package is definitely installed. Activating the venv first (`source .venv/bin/activate`)
works too.

**Check the port actually belongs to CanvasFlow.** If something else is already serving
on 3000, Next moves to 3001 and prints it — but a browser tab left open on 3000 will show
that other app instead, which looks like CanvasFlow rendering without styles. The page
title should read *CanvasFlow*.

If the frontend can't reach the backend, point it explicitly:

```bash
NEXT_PUBLIC_CANVASFLOW_API=http://localhost:8000 npx next dev -p 3000
```

</details>

Then hit the engine button in the bottom bar, pick an engine, and press **Run Workflow**.

### Engine-specific setup

**Option A — Anthropic API**
Get a key at https://console.anthropic.com. Paste it into Settings, or export
`ANTHROPIC_API_KEY` in `backend/.env` (the request-supplied key wins).

**Option B — Claude subscription bridge**
Install and log in to Claude Code once; the CLI keeps the session:

```bash
npm install -g @anthropic-ai/claude-code
claude          # follow the login prompt, then exit
claude -p "say hi"   # verify non-interactive mode works
```

Set the binary path in Settings if `claude` is not on the backend's `PATH`.

**Option C — Ollama**

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3
ollama serve    # http://localhost:11434
```

## Long sources

A one-hour video is ~78,000 characters of transcript. What happens to it depends on the
engine's context window, and the source node reports which path ran.

| Coverage | What it does | Cost |
| --- | --- | --- |
| **Auto** (default) | Whole source when it fits; relevant passages when it doesn't | 1 call |
| **Relevant** | Always retrieval — passages matching this node's instruction | 1 call |
| **Complete** | Reads every part in sequence, then synthesises. Nothing skipped | N+1 calls |

Retrieval ranks passages by semantic similarity when an embedding model is installed:

```bash
ollama pull nomic-embed-text     # optional, meaningfully better than the keyword fallback
```

Without it, retrieval falls back to BM25 keyword matching, which is weaker on paraphrase
("what did they say about pricing" vs a passage that says "costs $40/mo"). The node card
names which one ran.

### Ollama silently truncates by default

Ollama's default context window is **4096 tokens**, and it discards anything beyond that
*without an error* — the model just answers from a fragment of what you sent. CanvasFlow
sizes `num_ctx` per request from the actual prompt, capped by the model's real limit and
by `ollama_max_context_tokens` in Settings (default 32768).

A large window costs RAM even when unused. If Ollama starts swapping or the process gets
OOM-killed on long sources, lower that setting or use a smaller model.

## Workflow format

Graphs serialize to plain JSON (`{nodes, edges}`) — export/import from the control bar,
autosaved to LocalStorage. See `frontend/types.ts` for the schema.

## Repo layout

```
backend/main.py        FastAPI app: Pydantic models, topological executor, 3 engine adapters
frontend/types.ts      Node/canvas/engine type definitions (shared contract with backend)
frontend/components/   Canvas, CustomNodes, SettingsModal
frontend/lib/          API client + LocalStorage persistence
```

## License

MIT
