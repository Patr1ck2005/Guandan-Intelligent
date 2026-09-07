"""Web API robustness tests (Flask test client, no live server).

Covers the garbage-input acceptance audit: every previously-500ing request
shape must now return a graceful 200/400 with JSON, never an exception.
"""

import json

import pytest

from guandan.ui.web.app import app


@pytest.fixture()
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _post_json(client, path, payload):
    return client.post(path, data=json.dumps(payload),
                       content_type="application/json")


# ---------------------------------------------------------------------------
# /api/config — bad player keys and null params must not 500 (probe B)
# ---------------------------------------------------------------------------

def test_config_bad_player_key_returns_400(client, tmp_path, monkeypatch):
    from guandan.ui.web import app as webapp
    monkeypatch.setattr(webapp, "_CONFIG_PATH", tmp_path / "config.json")
    r = _post_json(client, "/api/config",
                   {"players": {"not-a-number": {"decider": "blind"}}})
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_config_out_of_range_player_returns_400(client, tmp_path, monkeypatch):
    from guandan.ui.web import app as webapp
    monkeypatch.setattr(webapp, "_CONFIG_PATH", tmp_path / "config.json")
    r = _post_json(client, "/api/config", {"players": {"9": {"decider": "blind"}}})
    assert r.status_code == 400


def test_config_null_params_are_stripped(client, tmp_path, monkeypatch):
    from guandan.ui.web import app as webapp
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(webapp, "_CONFIG_PATH", cfg_file)
    r = _post_json(client, "/api/config",
                   {"players": {"1": {"decider": "blind",
                                      "params": {"pass_threshold": None}}}})
    assert r.status_code == 200
    body = client.get("/api/config").get_json()
    params = body["config"]["players"]["1"].get("params", {})
    assert "pass_threshold" not in params or params["pass_threshold"] is not None
    # the on-disk snapshot (isolated to tmp) must be null-free too
    if cfg_file.exists():
        disk = json.loads(cfg_file.read_text(encoding="utf-8"))
        params_disk = disk["players"]["1"]["params"] or {}
        assert "pass_threshold" not in params_disk


def test_config_players_not_dict_returns_400(client, tmp_path, monkeypatch):
    from guandan.ui.web import app as webapp
    monkeypatch.setattr(webapp, "_CONFIG_PATH", tmp_path / "config.json")
    r = _post_json(client, "/api/config", {"players": [1, 2, 3]})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /api/play & /api/tribute_return — malformed bodies must not 500
# ---------------------------------------------------------------------------

def test_play_with_non_list_card_ids_is_tolerated(client):
    r = _post_json(client, "/api/play", {"card_ids": "abc"})
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("error") in (True, False)  # graceful state payload


def test_play_with_garbage_ids_is_tolerated(client):
    r = _post_json(client, "/api/play", {"card_ids": [99999, -1]})
    assert r.status_code == 200


def test_tribute_return_with_garbage_body_is_tolerated(client):
    r = _post_json(client, "/api/tribute_return", {"card_ids": {"a": 1}})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# /api/auto_play — guarded toggle (probe A/E)
# ---------------------------------------------------------------------------

def test_auto_play_enable_before_start_is_refused(client):
    # fresh session: game not started
    r = _post_json(client, "/api/new_game", {})
    assert r.status_code == 200
    r = _post_json(client, "/api/auto_play", {"enabled": True})
    body = r.get_json()
    assert body.get("auto_play") is False
    assert body.get("error") is True


def test_auto_play_response_shape(client):
    r = _post_json(client, "/api/auto_play", {"enabled": False})
    assert r.status_code == 200
    body = r.get_json()
    assert "auto_play" in body
    assert "error" in body


# ---------------------------------------------------------------------------
# /api/evaluate — off-turn guard (round-3 audit M1)
# ---------------------------------------------------------------------------

def test_evaluate_off_turn_returns_error_not_500(client):
    """The AI step state machine may be mid-move; evaluating must be refused
    with a graceful payload instead of racing the lock (a 6s MC read of
    hands used to run concurrently with a locked /api/state step)."""
    r = _post_json(client, "/api/evaluate", {"card_ids": [0, 1]})
    assert r.status_code == 200
    body = r.get_json()
    # Either the game hasn't started ("游戏未开始") or it isn't our turn —
    # never an exception / 500.
    assert body.get("error") in ("游戏未开始", "不是你的回合")
