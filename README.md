# Dagwood

*Layered agents, stacked in a DAG.*

A small, readable planner-executor system for LLM agents, built to learn (and teach) agent
orchestration and harness engineering.

You describe a task. A **planner** model turns it into a DAG of worker agents. You **review and
edit** the plan, then a **LangGraph executor** runs the agents in dependency order, in parallel
where it can. Each agent sees only the outputs of the steps it depends on. The UI shows the graph,
streams each agent's output, thoughts and tool calls live, and tracks tokens and cost.

```
task ──► planner ──► plan checks ──► human review ──► LangGraph executor ──► answer / PDF / slides
           ▲              │          (edit, re-route,     │   │   │
           └── retry ◄────┘           approve, reject)   agent agent agent  (each with tool + token caps)
```

**Stack:** Python 3.13 · [pydantic-ai](https://ai.pydantic.dev) agents · [LangGraph](https://langchain-ai.github.io/langgraph/)
execution · FastAPI + SSE · SQLite · React + React Flow · models on [DeepInfra](https://deepinfra.com)
(one API key) · optional tools via [Composio](https://composio.dev).

## What's in here

| Concern | Where | What to look at |
| --- | --- | --- |
| Planning | `orchestrator/factory.py` | Supervisor prompt; output validator that rejects bad plans (cycles, unknown tools, flat plans with no dependencies) and asks the model to retry |
| Plan schema | `orchestrator/schema.py` | `DAGNode` / `DAGPlan`, topological order, validation |
| Execution | `orchestrator/executor.py` | Compiles a plan into a LangGraph `StateGraph`; fan-out/fan-in; skipping downstream of failures; per-agent usage limits |
| Data flow between agents | `orchestrator/executor.py` | An agent's prompt contains only its `depends_on` outputs (truncated), not the whole run |
| Run lifecycle | `orchestrator/manager.py` | Background tasks, hard stop, per-user concurrency limit and FIFO queue |
| Model routing | `orchestrator/models.py` | Planner assigns each agent a tier (light/standard/heavy); a cost mode maps tiers to models |
| Cost estimates | `orchestrator/estimate.py`, `pricing.py` | Forecast learned from this server's own past runs (median and p90 per agent kind) |
| Tools | `orchestrator/tools.py`, `catalog.py`, `composio_source.py` | Built-in tools, a pluggable tool catalog, scoped per-agent MCP toolsets, destructive tools blocked |
| Live output | `orchestrator/live.py`, `api.py` | pydantic-ai stream events → in-memory feed → SSE |
| Persistence | `orchestrator/store.py` | Event log + SQLite snapshots; runs survive restarts |
| Deliverables | `orchestrator/documents.py` | Structured `DocumentSpec` → PDF (WeasyPrint, no network fetches), HTML slides, PPTX; charts dropped unless their numbers appear in the step's input |
| UI | `ui/src/` | Graph layout, plan editor, approval bar, live feed |

## Quick start

You need **Python 3.13**, **Node 20.19+ or 22.12+** (required by Vite 8), and a **DeepInfra API key**. That's the only key required.

### 1. Backend

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # then set DEEPINFRA_API_KEY
```

PDF output uses WeasyPrint, which needs Pango from your OS:

- Debian/Ubuntu/WSL: `sudo apt install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0`
- macOS: `brew install pango`
- Windows: use WSL

### 2. Run the tests (no keys needed)

```bash
python -m pytest -q tests
```

The suite uses pydantic-ai's test and function models, so it makes no network calls and costs
nothing. It's also a good way to read how each piece behaves.

### 3. Start it

```bash
uvicorn orchestrator.api:app --reload           # API on http://localhost:8000
```

```bash
cd ui
npm install
npm run dev                                     # UI on http://localhost:5173
```

Open http://localhost:5173 and type a task. Try **Economy** mode first. Typical runs there cost
a fraction of a cent: a 3-agent run cost $0.0005 at the prices below.

### Optional: web search and app tools (Composio)

Without `COMPOSIO_API_KEY`, agents only have the built-in tools (`calc`, `word_count`,
`add_note`). The orchestration still works, but agents answer from model knowledge alone. Add
a Composio key to `.env` to give agents web search by default, and to attach other toolkits
(GitHub, Gmail, ...) from the UI's Tools drawer. Tools that Composio marks as destructive are
blocked unless `COMPOSIO_ALLOW_DESTRUCTIVE=true`. `scripts/check_composio.py` checks your setup.

All other settings (models, limits, storage paths) are documented in `.env.example`.

## Limitations: read before you rely on it

This is a learning project, not a product.

- **Local only, no auth.** Every request is treated as the same user (`_current_user()` in
  `orchestrator/api.py` is where auth would go). Don't expose the server to a network.
- **Run data is stored in plain text.** `runs.db` holds your prompts and every agent's output,
  and `artifacts/` holds generated files. Both are git-ignored. Keep it that way.
- **Prices are dated.** Model prices in `orchestrator/models.py` were checked on 2026-10-09
  (DeepInfra) and 2026-10-12 (the comparison-only Claude/OpenAI/xAI/Gemini rows). Providers
  change prices, and model IDs get retired, so treat all costs as estimates.
- **The cost estimator runs low in known cases:**
  - Fan-in/synthesis agents are priced at a flat ~4k input tokens, regardless of how much
    upstream output they actually read.
  - Heavy-tier agents are priced from low-effort history.
  - Failed agents are left out of the history it learns from.
  - Publishing steps are priced like plain agents.
- **Premium model cap vs search agents.** Kimi K3 has a 100k input-token cap per agent. In
  practice, search agents often need 200k+ and hit the cap, so keep search steps on
  light/standard tiers.
- **Comparison models are estimate-only.** The Claude/OpenAI/xAI/Gemini prices appear in
  estimates, but there's no client for them; only DeepInfra models run.
- **Live output is best-effort.** If the model connection drops mid-stream, that agent fails
  (client retries only cover opening a request). The streamed feed is in memory only. The
  finished agent's output, thoughts and tool calls are what get saved.
- **PPTX output is untested visually.** It's generated and unit-tested, but it has never been
  opened in PowerPoint or LibreOffice during development.
- **No per-call approval for tools.** Destructive tools are blocked outright, not confirmed
  one call at a time.

## Project layout

```
orchestrator/   FastAPI app, planner, executor, routing, estimates, tools, documents
tests/          pytest suite (no network)
scripts/        live integration checks
ui/             React + Vite front end
```

## License

MIT. See [LICENSE](LICENSE).
