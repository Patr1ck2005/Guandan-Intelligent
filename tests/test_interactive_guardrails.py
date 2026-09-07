"""Guardrails for the interactive game state machine.

Regression tests for the button-level acceptance audit (M1 stabilization):
  - round settlement is idempotent (auto-play toggling at round-over must
    never double-advance team levels);
  - set_auto_play refuses illegal states (round over / pending tribute /
    before start);
  - an AI leader whose play is illegal or whose agent crashes is forced to
    play instead of freezing the round with a leader pass;
  - null values in saved AI configs cannot brick agent creation.
"""

import time
from types import SimpleNamespace

import pytest

from guandan.ui.interactive import InteractiveGame


def _fresh_game() -> InteractiveGame:
    g = InteractiveGame(level=2, agent_timeout_ms=0)
    g.start_new_round()
    return g


def _force_round_over(g: InteractiveGame) -> None:
    """Pretend three players finished so the round is settleable."""
    assert g.state is not None
    g.state.finished_positions = [1, 2, 0]
    g._finish_round()


# ---------------------------------------------------------------------------
# Idempotent round settlement (probe A: double settlement via auto-play toggle)
# ---------------------------------------------------------------------------

def test_finish_round_is_idempotent():
    g = _fresh_game()
    _force_round_over(g)
    levels = list(g.team_levels)
    n_results = len(g._round_results)

    g._finish_round()
    g._finish_round()

    assert list(g.team_levels) == levels
    assert len(g._round_results) == n_results


def test_rearmed_ai_after_round_over_does_not_resettle():
    """The old api_auto_play re-armed _ai_running while the round-over panel
    was up; each subsequent /api/state poll re-ran settlement."""
    g = _fresh_game()
    _force_round_over(g)
    levels = list(g.team_levels)
    n_results = len(g._round_results)

    g._ai_running = True  # simulate the old buggy re-arm
    g.get_state()
    g.get_state()

    assert list(g.team_levels) == levels
    assert len(g._round_results) == n_results
    assert g._ai_running is False


def test_set_auto_play_refused_at_round_over():
    g = _fresh_game()
    _force_round_over(g)

    state = g.set_auto_play(True)

    assert state["error"] is True
    assert state["auto_play"] is False
    assert g._auto_play is False
    assert g._ai_running is False

    # a state poll afterwards still settles nothing new
    g.get_state()
    assert len(g._round_results) == 1


def test_set_auto_play_refused_while_pending_tribute_return():
    g = _fresh_game()
    g._pending_return = SimpleNamespace(paid_card=None)

    state = g.set_auto_play(True)

    assert state["error"] is True
    assert g._auto_play is False
    assert g._ai_running is False


def test_set_auto_play_refused_before_start():
    g = InteractiveGame(level=2, agent_timeout_ms=0)
    g.start_new_round()
    g._game_started = False

    state = g.set_auto_play(True)

    assert state["error"] is True
    assert g._ai_running is False


def test_set_auto_play_disable_always_allowed():
    g = _fresh_game()
    g._auto_play = True
    g._ai_running = True

    state = g.set_auto_play(False)

    assert state["error"] is False
    assert g._auto_play is False


# ---------------------------------------------------------------------------
# AI leader cannot stall the round (probe B/audit: leader pass freezes play)
# ---------------------------------------------------------------------------

class _PassAgent:
    def choose_play(self, view):
        return []


class _BoomAgent:
    def choose_play(self, view):
        raise RuntimeError("boom")


def _seat_ai_leader(g: InteractiveGame) -> None:
    g._game_started = True
    g.state.current_player = 1
    g._ai_running = True


def test_ai_leader_illegal_pass_is_forced_to_play(monkeypatch):
    from guandan.ai import registry

    g = _fresh_game()
    _seat_ai_leader(g)
    monkeypatch.setattr(registry, "create_agent_for_player",
                        lambda cfg, pid: _PassAgent())

    g.get_state()

    # The trick must have a play on the table — never a leader pass.
    assert g.state.table.current_combo is not None
    assert g.state.table.trick_history
    first_player, first_combo = g.state.table.trick_history[0]
    assert first_player == 1 and first_combo is not None


def test_ai_agent_crash_falls_back_to_forced_play(monkeypatch):
    from guandan.ai import registry

    g = _fresh_game()
    _seat_ai_leader(g)
    monkeypatch.setattr(registry, "create_agent_for_player",
                        lambda cfg, pid: _BoomAgent())

    g.get_state()  # must not raise

    assert g.state.table.current_combo is not None


# ---------------------------------------------------------------------------
# Null-config immunity (probe E: null num_samples bricked /api/state)
# ---------------------------------------------------------------------------

def test_mc_agent_with_null_params_still_created():
    from guandan.ai.registry import create_agent_for_player

    cfg = {
        "players": {1: {"decider": "mc",
                        "params": {"num_samples": None, "time_limit_ms": None}}},
        "decider": {"default_model": "mc",
                    "model_params": {"mc": {"num_samples": 128,
                                            "time_limit_ms": 10000,
                                            "inner": "informed",
                                            "enumerator": "top_n",
                                            "sampler": "random"}}},
        "inner_model": {"model_params": {"informed": {"round_weight": None}}},
        "enumerator": {"model_params": {"top_n": {"top_n": None}}},
    }

    agent = create_agent_for_player(cfg, 1)  # must not raise
    assert agent is not None


def test_ai_params_from_dict_skips_none():
    from guandan.ai.params import AIParams

    p = AIParams.from_dict({"pass_threshold": None, "round_weight": None,
                            "efficiency_weight": 10.0})
    assert p.pass_threshold == 0.0   # default preserved
    assert p.round_weight == 8.0     # default preserved
    assert p.efficiency_weight == 10.0  # explicit value kept


def test_set_auto_play_resolves_pending_tribute_return():
    """Auto-play owns the human seat: a pending tribute return is resolved
    automatically (previously the toggle deadlocked — refused while the
    round could only proceed after the return)."""
    from guandan.card import Card

    g = _fresh_game()
    g._game_started = True
    paid = Card.from_id(52)  # small joker as the tribute card
    g._pending_return = SimpleNamespace(
        paid_card=paid, receiver=0, payer=1, returned_card=None)
    hand = tuple(Card.from_id(i) for i in (0, 13, 26))  # C2, C3, C4-ish
    g.state.hands = (hand,) + g.state.hands[1:]

    state = g.set_auto_play(True)

    assert state["error"] is False
    assert g._pending_return is None
    assert g._auto_play is True


def test_game_lock_serializes_public_api():
    g = _fresh_game()
    acquired = g._lock.acquire(timeout=1.0)
    try:
        assert acquired, "lock must be free outside of calls"
    finally:
        g._lock.release()


# ---------------------------------------------------------------------------
# Agent timeout must not hold the session lock (audit M1: H3)
# ---------------------------------------------------------------------------

class _SlowAgent:
    """A wedged agent: choose_play returns long after the caller's timeout
    fired (LLM retry loops can do this)."""

    def choose_play(self, view):
        time.sleep(1.5)
        return []


def test_agent_timeout_releases_lock_quickly(monkeypatch):
    """A timed-out agent must NOT block get_state for the rest of its own
    runtime: the old with-block ran shutdown(wait=True) while the timeout
    propagated, freezing every queued poll behind the wedged agent."""
    import guandan.ai.registry as registry_mod

    g = _fresh_game()
    g._game_started = True
    g.agent_timeout_ms = 150
    monkeypatch.setattr(registry_mod, "create_agent_for_player",
                        lambda cfg, pid: _SlowAgent())
    monkeypatch.setattr(g, "_force_play", lambda pid, hand: [])

    g._ai_running = True
    t0 = time.time()
    g.get_state()          # runs _step_one_ai → _ai_play(seat 1) → timeout
    wall = time.time() - t0
    # Timeout is 0.15s; the wedged agent sleeps 1.5s. With the fix the poll
    # returns right after the timeout; with the bug it blocks >= 1.5s.
    assert wall < 1.0, f"get_state blocked {wall:.2f}s behind wedged agent"
    assert g._ai_running is False


def test_playerview_carries_tribute_records():
    """interactive 的 PlayerView 必须携带公开的进贡记录（与 game.py 一致），
    否则网页对局里的 AI 比自对弈里的同一模型少一份公开信息。"""
    from guandan.ai.player_view import PlayerView
    from guandan.deck import Deck
    from guandan.game_state import GameState
    from guandan.ai.action_log import ActionLog
    import random as _random

    rng = _random.Random(11)
    hands = Deck.deal(Deck.shuffle(Deck.create(), rng=rng))
    state = GameState(level=2, round_number=1, hands=hands,
                      current_player=1, trick_number=1)
    tribute = [{"round": 1, "exchanges": [{"payer": 1, "receiver": 0}]}]
    view = PlayerView(state, 1, ActionLog(), tribute_records=tribute)
    assert view.tribute_public_records() == tribute


# ---------------------------------------------------------------------------
# Auto-play continuity across rounds (round-3 audit H4)
# ---------------------------------------------------------------------------

def test_auto_play_survives_next_round_no_start_click():
    """With auto-play armed, starting the next round must NOT drop back to
    the pre-start state: the overlay demanded one extra 开始游戏 click per
    round. game_started must stay true and the AI loop must re-arm."""
    g = _fresh_game()
    g._game_started = True
    g._auto_play = True

    g._start_round()  # what /api/new_round → start_new_round performs

    assert g._game_started is True
    assert g._ai_running is True
    assert g._pending_return is None


def test_manual_next_round_still_waits_for_start_click():
    """Manual mode keeps the old behavior: a fresh deal waits for the
    开始游戏 button."""
    g = _fresh_game()
    g._game_started = True
    g._auto_play = False

    g._start_round()

    assert g._game_started is False
    assert g._ai_running is False
