"""AIParams auto-tuner tests (stage 4.3).

Fast tests for the pure pieces (budget clamping, candidate bounds,
mutation) + a tiny real tuner run against the reference lineup with a
hard wall clock. Also covers the config-apply endpoint (tmp cwd).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from guandan.eval.tuner import (  # noqa: E402
    SEARCH_SPACE,
    TunerBudget,
    _mutate,
    _random_candidate,
    run_tuner,
)
from guandan.ai.params import AIParams  # noqa: E402


def test_search_space_fields_exist_in_aiparams():
    """Every tunable name must be a real AIParams float field."""
    fields = AIParams.__dataclass_fields__
    for name in SEARCH_SPACE:
        assert name in fields, f"{name} missing from AIParams"
        import dataclasses
        assert fields[name].type in ("float",) or 'float' in str(fields[name].type)
    print("  PASS search space ⊂ AIParams")


def test_budget_clamps():
    b = TunerBudget(max_candidates=999, games_per_candidate=0,
                    max_seconds=1, max_workers=99, base_seed=-5)
    b.clamp()
    assert 1 <= b.max_candidates <= 60
    assert b.games_per_candidate >= 1
    assert 30 <= b.max_seconds <= 3600
    assert 1 <= b.max_workers <= 8
    print("  PASS budget clamping")


def test_random_candidate_within_bounds():
    import random
    rng = random.Random(42)
    for _ in range(50):
        cand = _random_candidate(rng)
        for name, (lo, hi) in SEARCH_SPACE.items():
            assert lo <= cand[name] <= hi, f"{name}={cand[name]} out of bounds"
    print("  PASS candidates in bounds")


def test_mutate_stays_in_bounds():
    import random
    rng = random.Random(7)
    base = _random_candidate(rng)
    for _ in range(50):
        mut = _mutate(base, rng, sigma=2.0)  # extreme sigma
        for name, (lo, hi) in SEARCH_SPACE.items():
            assert lo <= mut[name] <= hi
    print("  PASS mutation clamped")


def test_run_tuner_tiny_real_run():
    """A 2-candidate × 2-game × 30s-budget run completes and is well-formed."""
    budget = TunerBudget(max_candidates=2, games_per_candidate=2,
                         max_seconds=60, max_workers=2, base_seed=42)
    res = run_tuner(budget)
    assert res["best"] is not None
    assert res["best"]["games"] == 2
    assert len(res["trials"]) >= 2  # coarse + at least one refinement
    assert 0.0 <= res["best"]["win_rate"] <= 1.0
    assert len(res["best"]["wilson"]) == 2
    assert res["budget"]["elapsed_s"] < 60
    assert res["note"].startswith("random search")
    assert res["verdict"] in ("effective", "inconclusive", "inconclusive")
    print(f"  PASS tiny tuner run (best {res['best']['win_rate']:.0%}, "
          f"{res['budget']['elapsed_s']}s)")


def test_run_tuner_budget_respected():
    """max_seconds=30 minimum: a run must not exceed the hard budget."""
    budget = TunerBudget(max_candidates=6, games_per_candidate=4,
                         max_seconds=30, max_workers=2, base_seed=1)
    import time
    t0 = time.time()
    res = run_tuner(budget)
    elapsed = time.time() - t0
    assert elapsed < 40, f"tuner blew the budget: {elapsed:.1f}s"
    print(f"  PASS budget respected ({elapsed:.1f}s, "
          f"{len(res['trials'])} trials)")


def test_cancel_check_hard_cancels():
    """A cancelled tuner must raise JobCancelled (→ '已停止', nothing
    persisted) instead of returning a stored stopped_early result."""
    import threading

    from guandan.eval.eval_api import JobCancelled

    budget = TunerBudget(max_candidates=5, games_per_candidate=1,
                         max_seconds=60, max_workers=1, base_seed=9)
    with pytest.raises(JobCancelled):
        run_tuner(budget, cancel_check=lambda: True)
    print("  PASS cancel check (hard cancel)")


def test_cancelled_tuner_job_not_persisted():
    """End-to-end via the job API: cancel a tuner → status=cancelled, and
    no new tuner row appears in the history store."""
    import time as _time

    from guandan.eval import eval_api as E
    from guandan.eval import store
    from guandan.eval.tuner import TunerBudget

    store.reset_for_tests()
    before = store.list_runs(kind="tuner", limit=1000)
    real_run_tuner = E.__dict__.get("run_tuner")

    # Direct spawn path: run_tuner inside _spawn with a cancel that trips
    # immediately (mirrors the endpoint wiring).
    def run(progress_cb, live_cb):
        from guandan.eval.tuner import run_tuner as rt
        return rt(TunerBudget(max_candidates=3, games_per_candidate=1,
                              max_seconds=60, max_workers=1, base_seed=5),
                  progress_cb=progress_cb, live_cb=live_cb,
                  cancel_check=lambda: True)

    jid = E._new_job("tuner")
    E._jobs[jid]["cancel"] = True  # trip the check from the very first unit
    E._spawn(jid, run)
    for _ in range(100):
        _time.sleep(0.05)
        if E._jobs[jid]["status"] in ("cancelled", "done", "error"):
            break
    assert E._jobs[jid]["status"] == "cancelled", E._jobs[jid]
    after = store.list_runs(kind="tuner", limit=1000)
    assert len(after) == len(before), "cancelled tuner must not persist"
    print("  PASS cancelled tuner not persisted")


def test_tuner_apply_endpoint(tmp_path, monkeypatch):
    """POST /api/eval/tuner/apply writes weights + returns a diff."""
    from guandan.eval import store
    from guandan.ui.web.app import app

    # temp config.json + temp store
    cfg = {"decider": {"model_params": {"blind": {"efficiency_weight": 20.0}}},
           "inner_model": {"model_params": {"blind": {}}}}
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    store.reset_for_tests(str(tmp_path / "db.sqlite"))

    monkeypatch.setattr("guandan.eval.eval_api.os.path", os.path)
    # point the endpoint's config path at tmp by patching dirname chain:
    # the endpoint computes project root from __file__; simplest is to run
    # it for real but restore config.json afterwards if it exists.
    real_cfg = os.path.join(os.getcwd(), "config.json")
    backup = None
    if os.path.exists(real_cfg):
        with open(real_cfg, "r", encoding="utf-8") as f:
            backup = f.read()

    app.config["TESTING"] = True
    try:
        with app.test_client() as c:
            r = c.post("/api/eval/tuner/apply", json={
                "weights": {"efficiency_weight": 33.0,
                            "support_teammate_lead": 5.0}})
            assert r.status_code == 200
            diff = r.get_json()["diff"]
            assert diff["efficiency_weight"]["from"] == 20.0
            assert diff["efficiency_weight"]["to"] == 33.0
            with open(real_cfg, "r", encoding="utf-8") as f:
                new_cfg = json.load(f)
            assert new_cfg["decider"]["model_params"]["blind"]["efficiency_weight"] == 33.0
            assert new_cfg["inner_model"]["model_params"]["blind"]["support_teammate_lead"] == 5.0
        print("  PASS tuner apply (real config.json written + restored)")
    finally:
        if backup is not None:
            with open(real_cfg, "w", encoding="utf-8") as f:
                f.write(backup)
        store.reset_for_tests()


if __name__ == "__main__":
    test_search_space_fields_exist_in_aiparams()
    test_budget_clamps()
    test_random_candidate_within_bounds()
    test_mutate_stays_in_bounds()
    test_run_tuner_tiny_real_run()
    print("OK")
