"""Regression baseline suite (stage 3.5).

Runs a fixed standard suite (curated-scenario benchmark with the
non-LLM reference profiles + a fixed-seed blind-vs-blind self-play
match) and compares the metrics against the saved baseline run in the
SQLite history store.

Thresholds are absolute percentage-point deltas:
  - benchmark accuracy per model: ±10 pp
  - match win share per side:     ±15 pp

Verdict semantics (honest):
  - "green"       — all metrics within tolerance
  - "red"         — at least one breach
  - "no-baseline" — nothing to compare against yet (first run)
"""

from __future__ import annotations

from typing import Optional

ACC_TOLERANCE_PP = 10.0
WIN_TOLERANCE_PP = 15.0

REFERENCE_PROFILES = [
    {"id": "blind", "label": "blind"},
    {"id": "mc", "label": "mc", "num_samples": 8, "time_limit_ms": 500,
     "inner": "blind", "enumerator": "fast"},
]


def collect_metrics(bench_result: dict, match_dict: dict) -> dict:
    """Extract the comparable metric dict from raw job results."""
    return {
        "benchmark": {
            str(row["model"]): {"accuracy": row.get("accuracy", 0.0),
                                "wilson": row.get("wilson")}
            for row in bench_result.get("summary", [])
        },
        "match": _match_share(match_dict),
    }


def _match_share(match_dict: dict) -> dict:
    n = (match_dict.get("a_wins") or 0) + (match_dict.get("b_wins") or 0)
    if n == 0:
        return {"a_share": None, "b_share": None, "n_games": 0,
                "errors": match_dict.get("errors", 0)}
    return {
        "a_share": round(100.0 * match_dict.get("a_wins", 0) / n, 1),
        "b_share": round(100.0 * match_dict.get("b_wins", 0) / n, 1),
        "n_games": n,
        "errors": match_dict.get("errors", 0),
    }


def compare_metrics(current: dict, baseline: dict) -> dict:
    """Pure comparison. Returns {verdict, breaches[], within_tolerances}.

    Missing baseline metrics are reported as "absent" (not a breach) so a
    growing metric list doesn't spuriously fail.
    """
    breaches = []
    checked = 0
    cur_bench = (current or {}).get("benchmark") or {}
    base_bench = (baseline or {}).get("benchmark") or {}
    for model, m in cur_bench.items():
        b = base_bench.get(model)
        if not b or b.get("accuracy") is None:
            continue
        checked += 1
        delta = m.get("accuracy", 0.0) - b["accuracy"]
        if abs(delta) > ACC_TOLERANCE_PP:
            breaches.append({
                "metric": f"benchmark.{model}.accuracy",
                "current": m.get("accuracy"), "baseline": b["accuracy"],
                "delta_pp": round(delta, 1),
                "tolerance_pp": ACC_TOLERANCE_PP,
            })
    cur_m = (current or {}).get("match") or {}
    base_m = (baseline or {}).get("match") or {}
    if cur_m.get("a_share") is not None and base_m.get("a_share") is not None:
        checked += 1
        delta = cur_m["a_share"] - base_m["a_share"]
        if abs(delta) > WIN_TOLERANCE_PP:
            breaches.append({
                "metric": "match.a_share",
                "current": cur_m["a_share"], "baseline": base_m["a_share"],
                "delta_pp": round(delta, 1),
                "tolerance_pp": WIN_TOLERANCE_PP,
            })
    verdict = "green" if not breaches else "red"
    if checked == 0:
        # Zero comparable metrics (e.g. baseline stored under different model
        # keys) — "green" here would be a false pass.
        verdict = "no-comparable-metrics"
    return {
        "verdict": verdict,
        "breaches": breaches,
        "metrics_checked": checked,
    }


def run_regression_suite(progress_cb=None, live_cb=None,
                         match_n_games: int = 3, base_seed: int = 4242) -> dict:
    """Run the standard suite and compare against the stored baseline."""
    from .scenario_bank import load_curated_scenarios
    from .eval_api import run_scenario_benchmark
    from .selfplay import run_match
    from . import store

    def _p(done, total, msg):
        if progress_cb:
            progress_cb(done, total, msg)

    _p(0, 100, "回归套件 · 场景基准（blind + mc）…")
    bench = run_scenario_benchmark(load_curated_scenarios(), REFERENCE_PROFILES,
                                   progress_cb=progress_cb, live_cb=live_cb)

    # Keep progress monotonic: the match phase owns 60..100 (scaled
    # per-game); the fixed 60 marker previously froze the bar for the whole
    # phase. progress_cb also carries the cancel check into run_match, so
    # 停止 takes effect during phase 2 (it used to be a silent no-op there).
    base = 60.0
    span = 40.0

    def _match_progress(done, total, msg):
        frac = (done / total) if total else 1.0
        _p(int(base + frac * span),
           100, f"回归套件 · 自对弈 {done}/{total} 局 · {msg}")

    def _match_live(detail):
        if live_cb:
            live_cb(detail)

    match = run_match({"id": "blind"}, {"id": "blind"},
                      n_games=match_n_games, base_seed=base_seed,
                      agent_timeout_ms=75000, max_workers=1,
                      progress_cb=_match_progress, live_cb=_match_live)

    current = collect_metrics(bench, match.to_dict())
    baseline_run = store.get_baseline("regression")
    if baseline_run is None:
        compare = {"verdict": "no-baseline", "breaches": [], "metrics_checked": 0}
    else:
        baseline_metrics = (baseline_run.get("summary") or {}).get("metrics") or {}
        compare = compare_metrics(current, baseline_metrics)

    return {
        "metrics": current,
        "compare": compare,
        "verdict": compare["verdict"],
        "baseline_run_id": baseline_run["id"] if baseline_run else None,
        "benchmark": {"summary": bench.get("summary", [])},
        "match": match.to_dict(),
        "thresholds": {"accuracy_pp": ACC_TOLERANCE_PP,
                       "win_share_pp": WIN_TOLERANCE_PP},
    }
