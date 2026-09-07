# CLAUDE.md — 掼蛋智能分析引擎 (Guandan Intelligent)

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
  ├─ TableState             — per-trick state (current combo, pass count, history)
  └─ ActionLog              — per-round public action memory, passed into PlayerView

AI System (guandan/ai/)     — layered, pluggable via registry + config.json
  Layer 0: hand_eval.py, action_log.py   — analysis + opponent tracking
  Layer 1: candidate_enum.py, combo_finder — candidate generation
  Layer 2: scorer.py, models/*           — heuristic scoring (teammate_context_score = 2v2 public-info heuristics)
  Layer 3: agent.py, mc_decider.py, ismcts.py — decision agents
  Layer 4: registry.py, params.py        — config + factory (AIParams includes teammate/opp-model weights, default 0)

Eval Lab (guandan/eval/)    — benchmark / selfplay / Elo / tuner / regression
  eval_api.py               — job API (heartbeat, stall watchdog, detail caps, auto-persist)
  selfplay.py               — matches; max_workers>1 = ProcessPoolExecutor, per-seed deterministic
  store.py                  — SQLite history (data/eval_history.db, WAL)
  regression.py             — standard suite vs saved baseline (±10pp acc / ±15pp win)
  tuner.py                  — AIParams random search + refinement, hard budget
  metrics.py, elo.py, scenario_bank.py

LLM Module (guandan/ai/llm/)  — end-to-end LLM-driven decisions
  config.py → client.py → prompts.py → parser.py → agent.py

Web UI (guandan/ui/web/)
  app.py (Flask routes) + arena_api.py + eval_api.py (Flask blueprints)
  templates/ (Jinja2 HTML), static/ (vanilla JS + CSS)
  static/lib/ (api.js, card.js, constants.js, store.js), eval.js/eval.css (Eval Lab)
```

## Key Conventions

- **Cards**: integer IDs 0–107 (two 54-card decks). `Card.from_id(n)` creates; `Card.display` renders.
- **Immutability**: `Card` and `Combo` use `@dataclass(frozen=True, slots=True)`. `GameState` is a plain (non-frozen) dataclass by convention — treat it as immutable and mutate only via `state_utils` helpers (`clone_state`, `apply_play`, …).
- **Agent interface**: `BaseAgent.choose_play(view: PlayerView) -> List[Card]`. Return `[]` to pass.
- **PlayerView**: filters opponent hands to sizes only — agents never see opponent cards. Samplers derive the opponent set from `view.player_id`; never hardcode seat numbers.
- **Registry pattern**: Add a model to `registry.py` SchemaBuilder → the config panel auto-discovers it. Note: arena.js `MODEL_INFO` and eval.js `MODEL_PARAMS` carry hardcoded UI metadata — new deciders/inner models need entries there too (descriptions/params only, no wiring).
- **Config**: `config.json` at project root. Schema-driven; defaults auto-generated from model params.
- **Logging**: `from guandan.logging import ai_log` — categories: game, ai, web, system. Ring buffer (10k entries) + file + console.
- **Deployment**: single-process only (Flask dev server / one Gunicorn worker). Session games, eval jobs, and config locks are in-process objects.

## AI Agent Types

| Agent | Decider ID | Description |
|-------|-----------|-------------|
| `HeuristicAgent` | `blind` | Greedy AIParams-weighted scoring (fast, no simulation) |
| `MonteCarloAgent` | `mc` | Sample opponent hands → simulate → vote by win rate |
| `ISMCTSDecider` | `ismcts` | UCB candidate evaluation: per-candidate win-rate simulation, robust-child pick (current implementation is 1-level UCB, no deep tree expansion) |
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

### Eval Lab (`eval_api.py`)
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/eval/models` | GET | Model schema for eval configs |
| `/api/eval/scenarios` | GET | Scenario bank (filterable) |
| `/api/eval/scenarios/generate` | POST | Generate synthetic scenarios (exact-solver labels) |
| `/api/eval/benchmark` | POST | Scenario benchmark (Wilson 95% CI in summary) |
| `/api/eval/match` | POST | Self-play match (`max_workers>1` = multiprocess) |
| `/api/eval/round_robin` | POST | Round-robin with Elo |
| `/api/eval/jobs/<id>` | GET | Job status (heartbeat/stall watchdog/detail caps) |
| `/api/eval/jobs/<id>/cancel` | POST | Cancel job (JobCancelled → JobInterrupted) |
| `/api/eval/history` | GET | Persisted runs (SQLite store) |
| `/api/eval/history/<id>` | GET | One run detail |
| `/api/eval/export/<id>` | GET | Export report (`format=json|csv|html`) |
| `/api/eval/regression` | POST | Standard regression suite vs saved baseline |
| `/api/eval/regression/latest` | GET | Latest regression verdict badge |
| `/api/eval/regression/baseline` | POST | Mark a run as the baseline |
| `/api/eval/tuner` | POST | AIParams auto-tuner (hard budget) |
| `/api/eval/tuner/best` | GET | Latest tuner best result |
| `/api/eval/tuner/apply` | POST | Write best weights into config.json (returns diff) |

## Important Notes

- `config.json` is **gitignored** (`.gitignore` has `*.json`) — it lives only
  on the local machine. API keys belong in env vars only (`DEEPSEEK_API_KEY`).
  Two endpoints write it (game-page `/api/config` POST and `/api/eval/tuner/apply`);
  both share `_CONFIG_WRITE_LOCK` and write atomically. Because it is not in
  git, a bad write cannot be reverted via git — the tuner-apply endpoint
  validates weight keys against the search space for this reason.
- The frontend uses **no framework** — all DOM manipulation is vanilla JS. Templates are Jinja2.
- `@dataclass(frozen=True, slots=True)` is the standard pattern for value objects.
- AI is timeboxed: 30s default per turn in game loop, configurable per agent.
- `state_utils.py` has shared helpers used by both `Game` and simulation code — be careful when modifying.
