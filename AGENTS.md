# AGENTS.md — 掼蛋智能分析引擎 (Guandan Intelligent)

Python-first Guandan card game engine + pluggable AI decision system + Flask web UI.

## Tech Stack

- **Python 3.11+** — core engine, AI, Flask server
- **Flask + Gunicorn** — web server (port 8765)
- **Vanilla JS** — frontend (no framework; ES6 modules in `static/lib/`)
- **pytest** — test suite
- **httpx** — HTTP client for LLM API calls
- **Playwright** (npm) — e2e UI verification scripts only

## Quick Commands

```bash
pip install -r requirements.txt        # install deps
python -m guandan.ui.web.app           # start web server (http://localhost:8765)
pytest                                  # run all tests
pytest tests/test_llm_agent.py -v      # run LLM-specific tests
```

## Architecture

```
Game Engine (game.py)       — round/trick loop, ThreadPoolExecutor timeout
  ├─ GameState (dataclass)  — full snapshot: hands, table, played, finished
  ├─ RulesEngine            — play validation gate
  ├─ tribute.py             — 进贡/还贡/抗贡 (post-round tribute flow)
  └─ TableState             — per-trick state (current combo, pass count, history)

AI System (guandan/ai/)     — layered, pluggable via registry + config.json
  Layer 0: hand_eval.py, action_log.py   — analysis + opponent tracking
  Layer 1: candidate_enum.py, combo_finder — candidate generation
  Layer 2: scorer.py, models/*           — heuristic scoring
  Layer 3: agent.py, mc_decider.py, ismcts.py — decision agents
  Layer 4: registry.py, params.py        — config + factory

LLM Module (guandan/ai/llm/)  — NEW: end-to-end LLM-driven decisions
  config.py → client.py → prompts.py → parser.py → agent.py

Web UI (guandan/ui/web/)
  app.py (Flask routes) + arena_api.py (Flask blueprint)
  templates/ (Jinja2 HTML), static/ (vanilla JS + CSS)
  static/lib/ (api.js, card.js, constants.js, store.js)
```

## Key Conventions

- **Cards**: integer IDs 0–107 (two 54-card decks). `Card.from_id(n)` creates; `Card.display` renders.
- **Immutability**: `Card`, `Combo`, and `GameState` use `@dataclass(frozen=True)`. State changes return new objects via `state_utils.clone_state()`.
- **Agent interface**: `BaseAgent.choose_play(view: PlayerView) -> List[Card]`. Return `[]` to pass.
- **PlayerView**: filters opponent hands to sizes only — agents never see opponent cards.
- **Registry pattern**: Add a model to `registry.py` SchemaBuilder → frontend auto-discovers (zero JS changes).
- **Config**: `config.json` at project root. Schema-driven; defaults auto-generated from model params.
- **Logging**: `from guandan.logging import ai_log` — categories: game, ai, web, system. Ring buffer (10k entries) + file + console.

## AI Agent Types

| Agent | Decider ID | Description |
|-------|-----------|-------------|
| `HeuristicAgent` | `blind` | Greedy AIParams-weighted scoring (fast, no simulation) |
| `MonteCarloAgent` | `mc` | Sample opponent hands → simulate → vote by win rate |
| `ISMCTSDecider` | `ismcts` | Info-set MCTS: UCB selection → expansion → simulation |
| `LLMAgent` | `llm` | **NEW** DeepSeek API end-to-end decision (candidate or free mode) |

### LLM Agent Setup

```bash
export DEEPSEEK_API_KEY="sk-..."
# Then select "LLM 大模型" in the frontend config panel for any player.
# Without the env var, LLMAgent falls back to HeuristicAgent automatically.
```

Candidate mode (default): pre-computes legal combos, LLM picks the best index — safe, token-efficient.
Free mode: LLM selects raw card IDs — more creative, needs RulesEngine validation pass.

## Test Patterns

- `tests/test_core.py` — engine, rules, combos, scoring
- `tests/test_ai.py` — agents, AI decision quality
- `tests/test_llm_agent.py` — LLM config/client/parser/prompts (unit tests, mock HTTP)
- `guandan/arena/scenarios.py` — 12 deduction scenarios for benchmark comparison

Tests use `Card.from_id(n)` to construct hands. Mock external services (HTTP) in LLM tests.

## API Endpoints Summary

### Game (`app.py`)
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/`, `/game`, `/arena` | GET | HTML pages |
| `/api/state` | GET | Current game state |
| `/api/play` | POST | Human plays cards |
| `/api/pass` | POST | Human passes |
| `/api/start_game`, `/api/new_game` | POST | Game lifecycle |
| `/api/new_round` | POST | Start next round |
| `/api/tribute_return` | POST | Human 还贡 (return one card to the tribute payer) |
| `/api/hint` | GET | Quick play suggestion |
| `/api/suggest` | GET | Full AI suggestion with win rates |
| `/api/evaluate` | POST | MC evaluation of selected cards |
| `/api/debug` | GET | Full hand info (debug mode) |
| `/api/log`, `/api/ai_log` | GET | Query log entries |
| `/api/config` | GET/POST | Read/save AI configuration |
| `/api/auto_play` | POST | Toggle auto-play mode |

### Arena (`arena_api.py`)
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/arena/models` | GET | Available AI models schema |
| `/api/arena/scenarios` | GET | List scenarios by category |
| `/api/arena/scenarios/<id>` | GET | Scenario detail |
| `/api/arena/scenarios/<id>/perspectives` | GET | 4-player perspectives |
| `/api/arena/analyze/scenario` | POST | Run models on scenario |
| `/api/arena/benchmark` | POST | Batch benchmark across scenarios |
| `/api/arena/sim/init` | POST | Init simulator from scenario |
| `/api/arena/sim/init_custom` | POST | Init from custom hands |
| `/api/arena/sim/state` | POST | Get simulator state |
| `/api/arena/sim/step` | POST | Execute one step |
| `/api/arena/sim/analyze` | POST | Analyze current sim state |
| `/api/arena/check_combo` | POST | Validate card combination |

## Important Notes

- `config.json` is committed — API keys belong in env vars only (`DEEPSEEK_API_KEY`).
- The frontend uses **no framework** — all DOM manipulation is vanilla JS. Templates are Jinja2.
- `@dataclass(frozen=True, slots=True)` is the standard pattern for value objects.
- AI is timeboxed: 30s default per turn in game loop, configurable per agent.
- `state_utils.py` has shared helpers used by both `Game` and simulation code — be careful when modifying.
