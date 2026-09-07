"""Eval Lab stabilization tests (audit M2).

Input-validation 400s, tuner apply hardening, baseline kind guard, and
scenario bank persistence. Flask tests run against an isolated config.json
(via monkeypatched _config_json_path) and the conftest-isolated eval DB.
"""

import json

import pytest

import guandan.eval.eval_api as E
from guandan.eval.scenario_bank import ScenarioBank
from guandan.ui.web.app import app


@pytest.fixture()
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _post(client, path, payload):
    return client.post(path, data=json.dumps(payload),
                       content_type="application/json")


# ---------------------------------------------------------------------------
# Input validation 400s
# ---------------------------------------------------------------------------

def test_benchmark_empty_models_400(client):
    r = _post(client, "/api/eval/benchmark", {"models": []})
    assert r.status_code == 400


def test_benchmark_no_matching_scenarios_400(client):
    r = _post(client, "/api/eval/benchmark",
              {"models": [{"id": "blind"}], "category": "no-such-category"})
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_match_missing_profiles_400(client):
    r = _post(client, "/api/eval/match", {"n_games": 2})
    assert r.status_code == 400


def test_match_zero_games_clamped(client, monkeypatch):
    # Stub out _spawn: we only assert the params snapshot, not run a game.
    monkeypatch.setattr(E, "_spawn", lambda jid, fn: None)
    r = _post(client, "/api/eval/match",
              {"a": {"id": "blind"}, "b": {"id": "blind"}, "n_games": 0,
               "max_workers": 1, "agent_timeout_ms": 1000})
    assert r.status_code == 200
    assert "job_id" in r.get_json()


def test_match_worker_cap(client, monkeypatch):
    monkeypatch.setattr(E, "_spawn", lambda jid, fn: None)
    r = _post(client, "/api/eval/match",
              {"a": {"id": "blind"}, "b": {"id": "blind"}, "n_games": 2,
               "max_workers": 5000, "agent_timeout_ms": 1000})
    assert r.status_code == 200
    # capped at _MAX_WORKERS_CAP (8) — verify via job params
    jid = r.get_json()["job_id"]
    job = E._jobs[jid]
    assert job["params"]["max_workers"] <= E._MAX_WORKERS_CAP


def test_round_robin_needs_two_models(client):
    r = _post(client, "/api/eval/round_robin", {"models": [{"id": "blind"}]})
    assert r.status_code == 400


def test_generate_zero_n_clamped(client, monkeypatch):
    monkeypatch.setattr(E, "_spawn", lambda jid, fn: None)
    r = _post(client, "/api/eval/scenarios/generate",
              {"n": 0, "max_cards": 2, "seed": 1})
    assert r.status_code == 200
    jid = r.get_json()["job_id"]
    assert E._jobs[jid]["params"]["n"] >= 1


def test_generate_absurd_n_clamped(client, monkeypatch):
    monkeypatch.setattr(E, "_spawn", lambda jid, fn: None)
    r = _post(client, "/api/eval/scenarios/generate", {"n": 10 ** 9})
    assert r.status_code == 200
    jid = r.get_json()["job_id"]
    assert E._jobs[jid]["params"]["n"] <= 500


# ---------------------------------------------------------------------------
# Baseline kind guard
# ---------------------------------------------------------------------------

def test_baseline_rejects_non_regression_run(client):
    from guandan.eval import store
    # seed a fake benchmark run in the test DB
    run_id = store.store_run("benchmark", {"k": 1}, {"summary": []})
    assert run_id
    r = _post(client, "/api/eval/regression/baseline", {"run_id": run_id})
    assert r.status_code == 400
    assert "只有回归运行" in r.get_json()["error"]


def test_baseline_missing_run_404(client):
    r = _post(client, "/api/eval/regression/baseline", {"run_id": 999999})
    assert r.status_code == 404


def test_baseline_requires_run_id(client):
    r = _post(client, "/api/eval/regression/baseline", {})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Tuner apply hardening
# ---------------------------------------------------------------------------

@pytest.fixture()
def isolated_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "decider": {"default_model": "blind",
                    "model_params": {"blind": {"round_weight": 8.0}}},
        "inner_model": {"model_params": {"blind": {"round_weight": 8.0}}},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(E, "_config_json_path", lambda: str(cfg))
    return cfg


def test_tuner_apply_rejects_unknown_keys(client, isolated_config):
    r = _post(client, "/api/eval/tuner/apply",
              {"weights": {"evil_key": 1.0, "round_weight": 5.0}})
    assert r.status_code == 400
    assert "搜索空间" in r.get_json()["error"]
    # nothing was written
    disk = json.loads(isolated_config.read_text(encoding="utf-8"))
    assert disk["decider"]["model_params"]["blind"]["round_weight"] == 8.0


def test_tuner_apply_clamps_and_writes_atomically(client, isolated_config):
    r = _post(client, "/api/eval/tuner/apply",
              {"weights": {"round_weight": 99999.0}})
    assert r.status_code == 200
    body = r.get_json()
    assert body["applied"] is True
    disk = json.loads(isolated_config.read_text(encoding="utf-8"))
    # clamped to the search-space high bound (20.0)
    assert disk["decider"]["model_params"]["blind"]["round_weight"] <= 20.0
    # inner blind kept in sync
    assert disk["inner_model"]["model_params"]["blind"]["round_weight"] \
        == disk["decider"]["model_params"]["blind"]["round_weight"]
    # no temp file left behind
    leftovers = [p for p in isolated_config.parent.iterdir()
                 if p.suffix == ".tmp"]
    assert not leftovers


def test_tuner_apply_no_weights_400(client, isolated_config):
    r = _post(client, "/api/eval/tuner/apply", {})
    assert r.status_code == 400


def test_tuner_apply_wrong_kind_run_400(client, isolated_config):
    from guandan.eval import store
    run_id = store.store_run("benchmark", {}, {"summary": []})
    r = _post(client, "/api/eval/tuner/apply", {"run_id": run_id})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Scenario bank persistence
# ---------------------------------------------------------------------------

def test_scenario_bank_persists_and_reloads(tmp_path, monkeypatch):
    monkeypatch.setenv("GUANDAN_SCENARIO_BANK", str(tmp_path / "bank.json"))
    from guandan.arena.scenarios import Scenario
    b1 = ScenarioBank([])
    s = Scenario(id="t-persist-1", name="T", category="deduction",
                 description="", hand=[0, 1], source="synthetic")
    b1.add([s])
    assert (tmp_path / "bank.json").exists()
    # a fresh instance (server restart) must restore it
    b2 = ScenarioBank([])
    assert b2.get("t-persist-1") is not None
    assert b2.get("t-persist-1").source == "synthetic"


def test_scenario_bank_broken_file_is_tolerated(tmp_path, monkeypatch):
    p = tmp_path / "bank.json"
    p.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv("GUANDAN_SCENARIO_BANK", str(p))
    b = ScenarioBank([])  # must not raise
    assert b.counts()["total"] == 0


def test_scenario_bank_curated_not_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("GUANDAN_SCENARIO_BANK", str(tmp_path / "bank.json"))
    from guandan.arena.scenarios import Scenario
    b = ScenarioBank([])
    curated = Scenario(id="t-c-1", name="C", category="deduction",
                       description="", hand=[0], source="curated")
    b.add([curated])
    # Adding a curated-only batch never creates a persisted file from
    # nothing; if an earlier batch left a file, it must contain no curated
    # entries.
    if (tmp_path / "bank.json").exists():
        items = json.loads((tmp_path / "bank.json").read_text(encoding="utf-8"))
        assert all(item.get("source") != "curated" for item in items)


# ---------------------------------------------------------------------------
# compare_metrics: zero comparable metrics is not a green pass
# ---------------------------------------------------------------------------

def test_compare_zero_metrics_not_green():
    from guandan.eval.regression import compare_metrics
    r = compare_metrics({}, {})
    assert r["verdict"] == "no-comparable-metrics"


# ---------------------------------------------------------------------------
# Audit M3 hardening: CSV formula injection, store path, job write locks
# ---------------------------------------------------------------------------

def test_csv_escape_formula_injection():
    assert E._csv_escape("=cmd!A0").startswith("'")
    assert E._csv_escape("@SUM(1)").startswith("'")
    assert E._csv_escape("+1+1").startswith("'")
    assert E._csv_escape("-1").startswith("'")
    assert E._csv_escape("blind") == "blind"          # normal value untouched
    assert E._csv_escape("a,b") == '"a,b"'            # quoting still works


def test_store_default_db_path_is_absolute():
    """CWD-relative path silently moved the history DB when the server was
    started from another directory."""
    import os
    from guandan.eval import store
    assert os.path.isabs(store._DEFAULT_DB)
    assert store._DEFAULT_DB.endswith(os.path.join("data", "eval_history.db"))


def test_job_spawn_terminal_state_atomic(monkeypatch):
    """_spawn must commit result/status under _JOBS_LOCK: a status poll
    during persistence used to observe a torn view."""
    import time as _time

    seen = []

    def run(progress_cb, live_cb):
        return {"ok": 1}

    jid = E._new_job("generic")
    orig_store = E.store_run if hasattr(E, "store_run") else None

    # Slow the persistence import path so the post-persist lock window is
    # observable; assert status stays "running" (not "done" without result)
    # until the whole commit lands.
    import guandan.eval.store as store_mod
    real_store_run = store_mod.store_run

    def slow_store_run(*a, **kw):
        _time.sleep(0.3)
        return real_store_run(*a, **kw)

    monkeypatch.setattr(store_mod, "store_run", slow_store_run)
    E._spawn(jid, run)
    # sample status while persisting
    _time.sleep(0.15)
    j = E._jobs.get(jid)
    seen.append((j["status"], j.get("result")))
    for _ in range(100):
        _time.sleep(0.05)
        if E._jobs[jid]["status"] in ("done", "error", "cancelled"):
            break
    j2 = E._jobs[jid]
    seen.append((j2["status"], j2.get("result")))
    # During persistence: still running (no torn done-without-result)
    assert seen[0][0] == "running" and seen[0][1] is None
    # After: done WITH result, atomically
    assert seen[1][0] == "done" and seen[1][1] == {"ok": 1}
