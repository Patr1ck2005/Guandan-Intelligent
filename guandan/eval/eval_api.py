"""Eval Lab API — scenario benchmark, self-play match, round-robin, generation.

All long-running operations (benchmark / match / round-robin / generation) run
in background threads keyed by a job id, polled via ``GET /api/eval/jobs/<id>``.
Each job reports granular ``progress`` (0-1) and a human-readable ``message``.
"""

from __future__ import annotations

import json
import threading
import time
import uuid

from flask import Blueprint, jsonify, request

from ..ai.registry import get_schema
from .common import _build_view, _make_arena_agent
from .metrics import agreement_rate, is_correct, wilson_interval
from .scenario_bank import get_bank, generate_synthetic_scenarios, scenario_json
from .selfplay import JobInterrupted, run_match, run_round_robin

eval_bp = Blueprint('eval_api', __name__)

# ------------------------------------------------------------------
# In-memory background jobs (mirrors arena's _simulators/_games pattern)
# ------------------------------------------------------------------

_jobs: dict = {}
_JOB_TTL = 30 * 60          # terminal jobs are dropped 30 min after finish
_JOB_LIMIT = 50             # keep at most this many jobs (evict terminal first)
_DETAIL_LIMIT = 256 * 1024  # live detail larger than this is replaced by a stub
_STALL_AFTER_S = 10 * 60    # running job without heartbeat for 10 min -> stalled


def _cleanup_jobs():
    """TTL sweep + stall watchdog + cap enforcement.

    Fix vs previous behavior: the TTL used to expire jobs by started_at,
    which deleted legitimately long-running jobs past 30 min. Now:
      - terminal jobs expire _JOB_TTL after they finished;
      - running jobs are never TTL-expired, but a heartbeat older than
        _STALL_AFTER_S marks them "stalled" (progress_cb refreshes the
        heartbeat, so an active job always looks fresh);
      - when the dict exceeds _JOB_LIMIT, oldest terminal jobs are evicted
        first (running jobs are never evicted).
    """
    now = time.time()
    # Stall watchdog
    for j in _jobs.values():
        if j["status"] == "running" and \
                now - (j.get("heartbeat") or j["started_at"]) > _STALL_AFTER_S:
            j["status"] = "stalled"
            j["message"] = "疑似停滞（10 分钟无进展）— 可停止后重试"
    # TTL expiry (terminal only)
    expired = [jid for jid, j in _jobs.items()
               if j["status"] not in ("running", "cancelling")
               and now - (j.get("finished_at") or j["started_at"]) > _JOB_TTL]
    for jid in expired:
        del _jobs[jid]
    # Cap enforcement: evict oldest terminal jobs first
    if len(_jobs) > _JOB_LIMIT:
        terminal = sorted(
            ((jid, j) for jid, j in _jobs.items()
             if j["status"] not in ("running", "cancelling")),
            key=lambda kv: kv[1].get("finished_at") or kv[1]["started_at"])
        for jid, _j in terminal:
            if len(_jobs) <= _JOB_LIMIT:
                break
            del _jobs[jid]


class JobCancelled(JobInterrupted):
    """Raised inside a background job when the user requests cancellation.

    Subclasses ``selfplay.JobInterrupted`` so match/round-robin loops can
    catch cancellation raised through progress_cb without importing the
    API layer (no circular import).
    """


def _new_job(kind: str = "generic") -> str:
    # Single-active-job policy: CPU-bound games share the GIL, so piling up
    # concurrent jobs starves every one of them (the "stuck" symptom).
    # Starting a new job cancels all still-running ones.
    for jid, j in _jobs.items():
        if j["status"] in ("running", "cancelling"):
            j["cancel"] = True
            j["status"] = "cancelling"
            j["message"] = "被新任务取代，正在停止…"
    _cleanup_jobs()
    jid = uuid.uuid4().hex
    _jobs[jid] = {"status": "running", "progress": 0.0, "message": "",
                  "detail": {}, "cancel": False, "result": None, "error": None,
                  "kind": kind, "heartbeat": time.time(),
                  "started_at": time.time(), "finished_at": None}
    return jid


def _set_progress(job_id: str, progress: float, message: str):
    job = _jobs.get(job_id)
    if job:
        job["progress"] = max(0.0, min(1.0, progress))
        job["message"] = message
        job["heartbeat"] = time.time()
        # A stalled job that reports progress again is alive again.
        if job["status"] == "stalled":
            job["status"] = "running"


def _set_detail(job_id: str, detail: dict):
    job = _jobs.get(job_id)
    if not job:
        return
    job["heartbeat"] = time.time()
    try:
        size = len(json.dumps(detail, default=str))
    except (TypeError, ValueError):
        size = -1
    if 0 < size > _DETAIL_LIMIT:
        # Keep the live dashboard light: replace an oversized detail with a
        # stub (progress bar + message still render fine).
        keys = sorted(detail.keys()) if isinstance(detail, dict) else None
        job["detail"] = {"truncated": True, "keys": keys, "approx_bytes": size}
        job["detail_truncated"] = True
    else:
        job["detail"] = detail


def _is_cancelled(job_id: str) -> bool:
    job = _jobs.get(job_id)
    return bool(job and job.get("cancel"))


def _spawn(jid: str, fn):
    def progress_cb(done, total, message):
        if _is_cancelled(jid):
            raise JobCancelled("cancelled")
        _set_progress(jid, done / total if total else 1.0, message)

    def live_cb(detail):
        _set_detail(jid, detail)

    def target():
        try:
            result = fn(progress_cb, live_cb)
            _jobs[jid]["result"] = result
            _jobs[jid]["status"] = "done"
            _jobs[jid]["progress"] = 1.0
            _jobs[jid]["message"] = "完成"
            # Auto-persist (never breaks the job): benchmarks/matches/RR
            # are recorded into the SQLite history store with their params.
            try:
                kind = _jobs[jid].get("kind", "generic")
                params = _jobs[jid].get("params") or {}
                from .store import store_run
                _jobs[jid]["run_id"] = store_run(kind, params, result)
            except Exception:
                pass
        except JobCancelled:
            _jobs[jid]["status"] = "cancelled"
            _jobs[jid]["message"] = "已停止"
        except Exception as e:
            _jobs[jid]["status"] = "error"
            _jobs[jid]["error"] = str(e)
        finally:
            _jobs[jid]["finished_at"] = time.time()
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

    jid = _new_job("generate")
    _jobs[jid]["params"] = {"n": n, "level": level, "max_cards": max_cards,
                            "seed": seed, "time_limit_ms": time_limit_ms}

    def gen(progress_cb, live_cb):
        scenarios = generate_synthetic_scenarios(
            n=n, level=level, max_cards_per_player=max_cards,
            seed=seed, time_limit_ms=time_limit_ms,
            progress_cb=progress_cb, live_cb=live_cb)
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

    jid = _new_job("benchmark")
    _jobs[jid]["params"] = {"models": model_profiles, "category": category,
                            "source": source, "difficulty": difficulty,
                            "max_scenarios": max_scenarios}

    def run(progress_cb, live_cb):
        return run_scenario_benchmark(scenarios, model_profiles,
                                      progress_cb=progress_cb, live_cb=live_cb)

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

    jid = _new_job("match")
    _jobs[jid]["params"] = {"a": profile_a, "b": profile_b, "n_games": n_games,
                            "seed": base_seed, "max_workers": max_workers}

    def run(progress_cb, live_cb):
        m = run_match(profile_a, profile_b, n_games=n_games, base_seed=base_seed,
                      agent_timeout_ms=agent_timeout_ms, max_workers=max_workers,
                      progress_cb=progress_cb, live_cb=live_cb)
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

    jid = _new_job("round_robin")
    _jobs[jid]["params"] = {"models": profiles, "n_games": n_games,
                            "seed": base_seed, "max_workers": max_workers}

    def run(progress_cb, live_cb):
        return run_round_robin(profiles, n_games=n_games, base_seed=base_seed,
                               agent_timeout_ms=agent_timeout_ms,
                               max_workers=max_workers,
                               progress_cb=progress_cb, live_cb=live_cb)

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
        "detail": job.get("detail") or {},
        "result": job["result"],
        "error": job["error"],
        "kind": job.get("kind", "generic"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "detail_truncated": bool(job.get("detail_truncated")),
        "run_id": job.get("run_id"),
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
# History (SQLite-backed, stage 3)
# ------------------------------------------------------------------

@eval_bp.route("/api/eval/history")
def history():
    from .store import list_runs
    kind = request.args.get("kind") or None
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except ValueError:
        limit = 50
    return jsonify({"runs": list_runs(kind=kind, limit=limit)})


@eval_bp.route("/api/eval/history/<int:run_id>")
def history_detail(run_id):
    from .store import get_run
    run = get_run(run_id)
    if run is None:
        return jsonify({"error": "run not found"}), 404
    return jsonify(run)


# ------------------------------------------------------------------
# Report export (stage 3.3)
# ------------------------------------------------------------------

@eval_bp.route("/api/eval/export/<int:run_id>")
def export_run(run_id):
    from .store import get_run
    from flask import Response
    run = get_run(run_id)
    if run is None:
        return jsonify({"error": "run not found"}), 404
    fmt = request.args.get("format", "json")
    if fmt == "json":
        return Response(
            json.dumps(run, ensure_ascii=False, indent=2, default=str),
            mimetype="application/json",
            headers={"Content-Disposition":
                     f"attachment; filename=guandan_run_{run_id}.json"})
    if fmt == "csv":
        lines = ["model_id,model_name,accuracy,elo,wins,losses,avg_ms"]
        for m in run.get("models", []):
            lines.append(",".join(str(x) if x is not None else ""
                                  for x in [m.get("model_id"),
                                            _csv_escape(m.get("model_name")),
                                            m.get("accuracy"), m.get("elo"),
                                            m.get("wins"), m.get("losses"),
                                            m.get("avg_ms")]))
        return Response("\n".join(lines), mimetype="text/csv",
                        headers={"Content-Disposition":
                                 f"attachment; filename=guandan_run_{run_id}.csv"})
    if fmt == "html":
        html = _render_report_html(run)
        return Response(html, mimetype="text/html")
    return jsonify({"error": f"unknown format: {fmt}"}), 400


def _csv_escape(v):
    if v is None:
        return ""
    s = str(v)
    if "," in s or '"' in s or "\n" in s:
        return '"' + s.replace('"', '""') + '"'
    return s


def _render_report_html(run: dict) -> str:
    """Self-contained single-file report: inline CSS + tables + params."""
    models = run.get("models", [])
    model_rows = "".join(
        f"<tr><td>{_h(m.get('model_id'))}</td><td>{_h(m.get('model_name'))}</td>"
        f"<td>{_h(m.get('accuracy'))}</td><td>{_h(m.get('elo'))}</td>"
        f"<td>{_h(m.get('wins'))} / {_h(m.get('losses'))}</td>"
        f"<td>{_h(m.get('avg_ms'))}</td></tr>"
        for m in models) or '<tr><td colspan="6">无模型数据</td></tr>'
    params = json.dumps(run.get("params") or {}, ensure_ascii=False,
                        indent=2, default=str)
    summary = json.dumps(run.get("summary") or {}, ensure_ascii=False,
                         indent=2, default=str)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<title>掼蛋评估报告 #{run.get('id')} · {_h(run.get('kind'))}</title>
<style>
body {{ font-family:'Segoe UI',system-ui,sans-serif; max-width:860px; margin:24px auto; padding:0 16px; color:#222; }}
h1 {{ font-size:1.3em; border-bottom:2px solid #daa520; padding-bottom:6px; }}
h2 {{ font-size:1.05em; color:#8a6508; margin-top:22px; }}
table {{ border-collapse:collapse; width:100%; font-size:0.9em; }}
th, td {{ border:1px solid #ddd; padding:5px 9px; text-align:left; }}
th {{ background:#f7f3e8; }}
pre {{ background:#f6f6f6; border:1px solid #e3e3e3; border-radius:6px; padding:10px; overflow-x:auto; font-size:0.8em; }}
.meta {{ color:#666; font-size:0.85em; }}
footer {{ margin-top:30px; color:#999; font-size:0.75em; border-top:1px solid #eee; padding-top:8px; }}
</style></head><body>
<h1>掼蛋评估报告 #{run.get('id')} — {_h(run.get('kind'))}</h1>
<p class="meta">时间(UTC): {_h(run.get('ts_utc'))} · git: {_h(run.get('git_commit')) or '-'} ·
seed: {_h(run.get('seed'))} · notes: {_h(run.get('notes')) or '-'}</p>
<h2>模型明细</h2>
<table><tr><th>模型ID</th><th>名称</th><th>准确率</th><th>Elo</th><th>胜/负</th><th>平均耗时(ms)</th></tr>
{model_rows}</table>
<h2>参数快照</h2><pre>{_h(params)}</pre>
<h2>结果摘要</h2><pre>{_h(summary)}</pre>
<footer>Guandan Intelligent · 自包含单文件报告 · 生成于 {_h(run.get('ts_utc'))}</footer>
</body></html>"""


def _h(v) -> str:
    """HTML-escape any value for the report template."""
    from markupsafe import escape
    return str(escape(v if v is not None else "-"))


# ------------------------------------------------------------------
# Regression baseline (stage 3.5)
# ------------------------------------------------------------------

@eval_bp.route("/api/eval/regression", methods=["POST"])
def run_regression():
    from .regression import run_regression_suite
    data = request.get_json() or {}
    match_n = max(1, min(int(data.get("match_n_games", 3)), 10))
    base_seed = int(data.get("seed", 4242))

    jid = _new_job("regression")
    _jobs[jid]["params"] = {"match_n_games": match_n, "seed": base_seed,
                            "reference": "blind+mc benchmark, blind-vs-blind match"}

    def run(progress_cb, live_cb):
        return run_regression_suite(progress_cb=progress_cb, live_cb=live_cb,
                                    match_n_games=match_n, base_seed=base_seed)

    _spawn(jid, run)
    return jsonify({"job_id": jid})


@eval_bp.route("/api/eval/regression/latest")
def regression_latest():
    from .store import list_runs
    runs = list_runs(kind="regression", limit=1)
    if not runs:
        return jsonify({"run": None})
    r = runs[0]
    return jsonify({"run": r, "verdict": (r.get("summary") or {}).get("verdict")})


@eval_bp.route("/api/eval/regression/baseline", methods=["POST"])
def regression_save_baseline():
    from .store import save_baseline
    data = request.get_json() or {}
    run_id = int(data.get("run_id", 0))
    if not run_id:
        return jsonify({"error": "run_id required"}), 400
    ok = save_baseline(run_id)
    if not ok:
        return jsonify({"error": "run not found"}), 404
    return jsonify({"saved": True, "run_id": run_id})


# ------------------------------------------------------------------
# Benchmark runner (module-level so tests can import it)
# ------------------------------------------------------------------

def run_scenario_benchmark(scenarios, model_profiles, perspective: int = 0,
                           progress_cb=None, live_cb=None) -> dict:
    """Run models across scenarios. ``progress_cb(done, total, message)`` is
    called after each (scenario, model) is evaluated; ``live_cb(detail)``
    carries the partial leaderboard + current cell for the live dashboard."""
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
            if live_cb:
                partial = [{
                    "model": mid_, "model_name": s["name"],
                    "accuracy": round(s["correct"] / s["total"] * 100, 1),
                    "wilson": [round(v * 100, 1) for v in wilson_interval(s["correct"], s["total"])],
                    "correct": s["correct"], "total": s["total"],
                    "avg_ms": round(s["ms"] / s["total"], 1),
                } for mid_, s in by_model.items()]
                partial.sort(key=lambda x: x["accuracy"], reverse=True)
                last = results[-1] if results else {}
                live_cb({
                    "si": si + 1, "n_scenarios": len(scenarios),
                    "scenario_name": scenario.name,
                    "mi": mi + 1, "n_models": len(model_profiles),
                    "model_name": name,
                    "done": done, "total": total,
                    "last_correct": bool(last.get("correct")),
                    "last_ms": round(float(last.get("elapsed_ms") or 0), 1),
                    "matrix": [[r.get("correct"), r.get("error")]
                               for r in results],
                    "partial_summary": partial,
                })

    summary = []
    for mid, s in by_model.items():
        accuracy = round(s["correct"] / s["total"] * 100, 1) if s["total"] else 0
        lo, hi = wilson_interval(s["correct"], s["total"])
        summary.append({
            "model": mid, "model_name": s["name"],
            "accuracy": accuracy,
            "wilson": [round(lo * 100, 1), round(hi * 100, 1)],  # 95% CI (pp)
            "agreement_rate": (round(agreement_rate(s.get("labels", [])), 3)
                               if s.get("labels") else None),
            "avg_ms": round(s["ms"] / s["total"], 1) if s["total"] else 0,
            "correct": s["correct"], "total": s["total"],
        })
    summary.sort(key=lambda x: x["accuracy"], reverse=True)
    return {"results": results, "summary": summary}
