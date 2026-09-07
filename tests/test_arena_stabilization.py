"""Arena stabilization tests (audit M3).

Covers: simulator step validation, check_combo hardening, scenario init
error paths, and the unknown-model whitelist. All Flask tests use the app
test client; no live server involved.
"""

import json

import pytest

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
# check_combo hardening
# ---------------------------------------------------------------------------

def test_check_combo_rejects_out_of_range_id(client):
    r = _post(client, "/api/arena/check_combo", {"card_ids": [999], "level": 2})
    body = r.get_json()
    assert r.status_code == 400
    assert body["valid"] is False


def test_check_combo_rejects_negative_id(client):
    r = _post(client, "/api/arena/check_combo", {"card_ids": [-5], "level": 2})
    assert r.status_code == 400


def test_check_combo_rejects_bad_level(client):
    r = _post(client, "/api/arena/check_combo", {"card_ids": [0], "level": 99})
    assert r.status_code == 400


def test_check_combo_rejects_duplicates(client):
    r = _post(client, "/api/arena/check_combo", {"card_ids": [5, 5], "level": 2})
    assert r.status_code == 400


def test_check_combo_rejects_non_list(client):
    r = _post(client, "/api/arena/check_combo", {"card_ids": "abc", "level": 2})
    assert r.status_code == 400


def test_check_combo_accepts_valid_pair(client):
    # two cards of the same rank (2): ids 0 and 13
    r = _post(client, "/api/arena/check_combo", {"card_ids": [0, 13], "level": 2})
    body = r.get_json()
    assert r.status_code == 200
    assert body["valid"] is True
    assert body["combo_type"] == "对子"


# ---------------------------------------------------------------------------
# Simulator step validation
# ---------------------------------------------------------------------------

def test_sim_step_play_with_foreign_card_id_400(client):
    # init a scenario from the curated bank
    r = client.get("/api/arena/scenarios")
    sid = r.get_json()["scenarios"][0]["id"]
    r = _post(client, "/api/arena/sim/init", {"scenario_id": sid})
    assert r.status_code == 200
    state = r.get_json()
    sim_id = state["sim_id"]
    held = {c["id"] for c in state["players"][0]["hand"]}
    foreign = next(i for i in range(108) if i not in held)
    r2 = _post(client, "/api/arena/sim/step",
               {"sim_id": sim_id, "card_ids": [foreign]})
    assert r2.status_code == 400
    assert "error" in r2.get_json()


# ---------------------------------------------------------------------------
# Unknown model id → per-model error, not a silent blind run
# ---------------------------------------------------------------------------

def test_analyze_scenario_unknown_model_reported(client):
    # find any real scenario id via the list endpoint
    r = client.get("/api/arena/scenarios")
    scenarios = r.get_json()["scenarios"]
    assert scenarios, "scenario bank must not be empty"
    sid = scenarios[0]["id"]
    r = _post(client, "/api/arena/analyze/scenario",
              {"scenario_id": sid, "models": [{"id": "typo-model"}],
               "perspective": 0})
    assert r.status_code == 200
    results = r.get_json()["results"]
    assert results and results[0].get("error") == "Unknown model"
    # crucially: no choice pretending to be a blind result
    assert "choice" not in results[0]


def test_analyze_scenario_bad_perspective_400(client):
    r = client.get("/api/arena/scenarios")
    sid = r.get_json()["scenarios"][0]["id"]
    r = _post(client, "/api/arena/analyze/scenario",
              {"scenario_id": sid, "models": [{"id": "blind"}],
               "perspective": 11})
    assert r.status_code == 400


def test_analyze_scenario_empty_models_400(client):
    r = client.get("/api/arena/scenarios")
    sid = r.get_json()["scenarios"][0]["id"]
    r = _post(client, "/api/arena/analyze/scenario",
              {"scenario_id": sid, "models": [], "perspective": 0})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# from_custom: illegal table combo → 400 (no silent card vanishing)
# ---------------------------------------------------------------------------

def test_init_custom_illegal_table_combo_400(client):
    # [1]=C3, [15]=D4 → not a legal combo; must 400 (cards used to vanish)
    r = _post(client, "/api/arena/sim/init_custom", {
        "hands": {"0": [2], "1": [16], "2": [29], "3": [42]},
        "table": [1, 15],
        "table_player": 1,
        "current_player": 0,
        "level": 2,
    })
    assert r.status_code == 400
    assert "合法牌型" in r.get_json()["error"]


def test_init_custom_valid_table_pair_ok(client):
    # [1]=C3, [14]=D3 (deck1 idx14 → rank 3) → legal PAIR of 3s
    r = _post(client, "/api/arena/sim/init_custom", {
        "hands": {"0": [2], "1": [16], "2": [29], "3": [42]},
        "table": [1, 14],
        "table_player": 1,
        "current_player": 0,
        "level": 2,
    })
    assert r.status_code == 200
    state = r.get_json()
    assert state["sim_id"]
    # the table combo is on the table, not vanished
    assert state["table_combo"] is not None


def test_sim_step_missing_card_ids_400(client):
    r = client.get("/api/arena/scenarios")
    sid = r.get_json()["scenarios"][0]["id"]
    r = _post(client, "/api/arena/sim/init", {"scenario_id": sid})
    sim_id = r.get_json()["sim_id"]
    r = _post(client, "/api/arena/sim/step", {"sim_id": sim_id, "card_ids": []})
    assert r.status_code == 400
