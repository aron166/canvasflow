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
# 1. Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # optional: set ANTHROPIC_API_KEY
uvicorn main:app --reload --port 8000

# 2. Frontend (new terminal)
cd frontend
npm install
npm run dev                     # http://localhost:3000
```

Open http://localhost:3000, hit the gear icon, pick an engine, and press **Run Workflow**.

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
