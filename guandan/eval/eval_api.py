"""Eval Lab API — scenario benchmark, self-play match, round-robin, generation.

All long-running operations (benchmark / match / round-robin / generation) run
in background threads keyed by a job id, polled via ``GET /api/eval/jobs/<id>``.
Each job reports granular ``progress`` (0-1) and a human-readable ``message``.
"""

from __future__ import annotations

import threading
import time
import uuid

from flask import Blueprint, jsonify, request

from ..ai.registry import get_schema
from .common import _build_view, _make_arena_agent
from .metrics import is_correct
from .scenario_bank import get_bank, generate_synthetic_scenarios, scenario_json
from .selfplay import run_match, run_round_robin

eval_bp = Blueprint('eval_api', __name__)

# ------------------------------------------------------------------
# In-memory background jobs (mirrors arena's _simulators/_games pattern)
# ------------------------------------------------------------------

_jobs: dict = {}
_JOB_TTL = 30 * 60


def _cleanup_jobs():
    now = time.time()
    expired = [jid for jid, j in _jobs.items() if now - j["started_at"] > _JOB_TTL]
    for jid in expired:
        del _jobs[jid]


class JobCancelled(Exception):
    """Raised inside a background job when the user requests cancellation."""


def _new_job() -> str:
    jid = uuid.uuid4().hex
    _jobs[jid] = {"status": "running", "progress": 0.0, "message": "",
                  "cancel": False, "result": None, "error": None,
                  "started_at": time.time()}
    return jid


def _set_progress(job_id: str, progress: float, message: str):
    job = _jobs.get(job_id)
    if job:
        job["progress"] = max(0.0, min(1.0, progress))
        job["message"] = message


def _is_cancelled(job_id: str) -> bool:
    job = _jobs.get(job_id)
    return bool(job and job.get("cancel"))


def _spawn(jid: str, fn):
    def progress_cb(done, total, message):
        if _is_cancelled(jid):
            raise JobCancelled("cancelled")
        _set_progress(jid, done / total if total else 1.0, message)

    def target():
        try:
            _jobs[jid]["result"] = fn(progress_cb)
            _jobs[jid]["status"] = "done"
            _jobs[jid]["progress"] = 1.0
            _jobs[jid]["message"] = "完成"
        except JobCancelled:
            _jobs[jid]["status"] = "cancelled"
            _jobs[jid]["message"] = "已停止"
        except Exception as e:
            _jobs[jid]["status"] = "error"
            _jobs[jid]["error"] = str(e)
    threading.Thread(target=target, daemon=True).start()


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@eval_bp.route("/api/eval/models")
def list_models():
    schema = get_schema()
    decider = schema.get("decider", {}).get("models", {})
    inner = schema.get("inner_model", {}).get("models", {})
    enumerator = schema.get("enumerator", {}).get("models", {})
    return jsonify({
        "outer": [{"id": mid, "name": m["name"], "description": m.get("description", ""),
                   "is_mc": m.get("is_mc", False)} for mid, m in decider.items()],
        "inner": [{"id": mid, "name": m["name"], "description": m.get("description", "")}
                  for mid, m in inner.items()],
        "enumerator": [{"id": mid, "name": m["name"], "description": m.get("description", "")}
                       for mid, m in enumerator.items()],
    })


@eval_bp.route("/api/eval/scenarios")
def list_scenarios():
    bank = get_bank()
    args = request.args
    scenarios = bank.filter(
        category=args.get("category") or None,
        source=args.get("source") or None,
        difficulty=args.get("difficulty") or None,
        level=int(args.get("level")) if args.get("level") else None,
    )
    return jsonify({
        "scenarios": [scenario_json(s) for s in scenarios],
        "counts": bank.counts(),
    })


@eval_bp.route("/api/eval/scenarios/generate", methods=["POST"])
def generate_scenarios():
    data = request.get_json() or {}
    n = int(data.get("n", 50))
    level = int(data.get("level", 2))
    max_cards = int(data.get("max_cards", 4))
    seed = int(data.get("seed", 0))
    time_limit_ms = int(data.get("time_limit_ms", 5000))

    jid = _new_job()

    def gen(progress_cb):
        scenarios = generate_synthetic_scenarios(
            n=n, level=level, max_cards_per_player=max_cards,
            seed=seed, time_limit_ms=time_limit_ms, progress_cb=progress_cb)
        get_bank().add(scenarios)
        return {"generated": len(scenarios),
                "scenarios": [scenario_json(s) for s in scenarios]}

    _spawn(jid, gen)
    return jsonify({"job_id": jid})


@eval_bp.route("/api/eval/benchmark", methods=["POST"])
def benchmark():
    data = request.get_json() or {}
    model_profiles = data.get("models", [{"id": "blind"}])
    category = data.get("category")
    source = data.get("source")
    difficulty = data.get("difficulty")
    max_scenarios = int(data.get("max_scenarios", 0))  # 0 = all

    scenarios = get_bank().filter(category=category, source=source, difficulty=difficulty)
    if max_scenarios:
        scenarios = scenarios[:max_scenarios]

    jid = _new_job()

    def run(progress_cb):
        return run_scenario_benchmark(scenarios, model_profiles, progress_cb=progress_cb)

    _spawn(jid, run)
    return jsonify({"job_id": jid})


@eval_bp.route("/api/eval/match", methods=["POST"])
def match():
    data = request.get_json() or {}
    profile_a = data.get("a") or {"id": "blind"}
    profile_b = data.get("b") or {"id": "mc"}
    n_games = int(data.get("n_games", 10))
    base_seed = int(data.get("seed", 0))
    agent_timeout_ms = float(data.get("agent_timeout_ms", 75000))
    max_workers = int(data.get("max_workers", 1))

    jid = _new_job()

    def run(progress_cb):
        m = run_match(profile_a, profile_b, n_games=n_games, base_seed=base_seed,
                      agent_timeout_ms=agent_timeout_ms, max_workers=max_workers,
                      progress_cb=progress_cb)
        return m.to_dict()

    _spawn(jid, run)
    return jsonify({"job_id": jid})


@eval_bp.route("/api/eval/round_robin", methods=["POST"])
def round_robin():
    data = request.get_json() or {}
    profiles = data.get("models") or [{"id": "blind"}, {"id": "mc"}]
    n_games = int(data.get("n_games", 10))
    base_seed = int(data.get("seed", 0))
    agent_timeout_ms = float(data.get("agent_timeout_ms", 75000))
    max_workers = int(data.get("max_workers", 1))

    jid = _new_job()

    def run(progress_cb):
        return run_round_robin(profiles, n_games=n_games, base_seed=base_seed,
                               agent_timeout_ms=agent_timeout_ms,
                               max_workers=max_workers, progress_cb=progress_cb)

    _spawn(jid, run)
    return jsonify({"job_id": jid})


@eval_bp.route("/api/eval/jobs/<job_id>")
def job_status(job_id):
    _cleanup_jobs()
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "job_id": job_id,
        "status": job["status"],
        "progress": job["progress"],
        "message": job.get("message", ""),
        "result": job["result"],
        "error": job["error"],
    })


@eval_bp.route("/api/eval/jobs/<job_id>/cancel", methods=["POST"])
def cancel_job(job_id):
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    job["cancel"] = True
    if job["status"] == "running":
        job["status"] = "cancelling"
        job["message"] = "正在停止…"
    return jsonify({"job_id": job_id, "status": job["status"]})


# ------------------------------------------------------------------
# Benchmark runner (module-level so tests can import it)
# ------------------------------------------------------------------

def run_scenario_benchmark(scenarios, model_profiles, perspective: int = 0,
                           progress_cb=None) -> dict:
    """Run models across scenarios. ``progress_cb(done, total, message)`` is
    called after each (scenario, model) is evaluated."""
    results = []
    by_model = {}
    total = len(scenarios) * len(model_profiles)
    done = 0

    for si, scenario in enumerate(scenarios):
        view = _build_view(scenario, perspective)
        for mi, entry in enumerate(model_profiles):
            mid = entry.get("id", entry) if isinstance(entry, dict) else entry
            label = entry.get("label") if isinstance(entry, dict) else None
            agent, name = _make_arena_agent(entry if isinstance(entry, dict) else {"id": mid})
            name = label or name or mid

            if agent is None:
                results.append({"scenario_id": scenario.id, "scenario_name": scenario.name,
                                "model": mid, "model_name": name, "error": "unknown model",
                                "correct": False, "elapsed_ms": 0})
            else:
                t0 = time.time()
                try:
                    r = agent.analyze(view)
                except Exception as e:
                    results.append({"scenario_id": scenario.id, "scenario_name": scenario.name,
                                    "model": mid, "model_name": name, "error": str(e),
                                    "correct": False, "elapsed_ms": 0})
                else:
                    elapsed = (time.time() - t0) * 1000
                    choice = r.choice
                    correct = is_correct(
                        choice.card_ids if choice else [], scenario.expected_play,
                        scenario.expected_pass, r.pass_chosen)

                    results.append({
                        "scenario_id": scenario.id, "scenario_name": scenario.name,
                        "category": scenario.category, "model": mid, "model_name": name,
                        "choice_type": choice.combo_type if choice else "NONE",
                        "chosen_cards": choice.cards if choice else [],
                        "pass_chosen": r.pass_chosen, "correct": correct,
                        "elapsed_ms": r.metrics.get("elapsed_ms", elapsed),
                        "fallback": bool(r.metrics.get("fallback", False)),
                        "difficulty": scenario.difficulty,
                    })

                    s = by_model.setdefault(mid, {"correct": 0, "total": 0, "ms": 0.0, "name": name})
                    s["correct"] += int(correct)
                    s["total"] += 1
                    s["ms"] += elapsed

            done += 1
            if progress_cb:
                progress_cb(done, total,
                            f"场景 {si + 1}/{len(scenarios)} · {scenario.name} · "
                            f"模型 {mi + 1}/{len(model_profiles)} · {name}")

    summary = []
    for mid, s in by_model.items():
        summary.append({
            "model": mid, "model_name": s["name"],
            "accuracy": round(s["correct"] / s["total"] * 100, 1) if s["total"] else 0,
            "avg_ms": round(s["ms"] / s["total"], 1) if s["total"] else 0,
            "correct": s["correct"], "total": s["total"],
        })
    summary.sort(key=lambda x: x["accuracy"], reverse=True)
    return {"results": results, "summary": summary}
