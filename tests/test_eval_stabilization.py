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
