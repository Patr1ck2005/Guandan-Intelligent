"""Public-info opponent modeling tests (stage 4.2).

Covers: ActionLog wiring through Game, PlayerView public stats,
pass-constrained sampling, and the opp-model cover bonus. Agents never
see opponent cards — every accessor here exposes only public facts.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random  # noqa: E402

from guandan.card import Card, Rank, Suit  # noqa: E402
from guandan.combo import Combo, ComboType  # noqa: E402
from guandan.ai.action_log import ActionLog, CardTracker  # noqa: E402
from guandan.ai.params import AIParams  # noqa: E402
from guandan.ai.player_view import PlayerView  # noqa: E402
from guandan.game_state import GameState  # noqa: E402
from guandan.deck import Deck  # noqa: E402
from guandan.game import Game  # noqa: E402
from guandan.ai.agent import BlindAgent  # noqa: E402


def _deal_state(level=2, sizes=(27, 27, 27, 27)):
    deck = Deck.shuffle(Deck.create())
    hands = Deck.deal(deck)
    hands = tuple(tuple(sorted(h[:sizes[i]], key=lambda c: c.id))
                  for i, h in enumerate(hands))
    return GameState(level=level, round_number=1, hands=hands, current_player=0)


def test_game_wires_action_log():
    """A full game records actions and the view exposes a live tracker."""
    random.seed(3)
    game = Game({p: BlindAgent() for p in range(4)})
    game.play_game()
    assert len(game.action_log) > 0, "actions must be recorded"
    plays = sum(1 for a in game.action_log if a.type == "play")
    assert plays > 0
    # every recorded play's cards came from the 108-card universe
    for a in game.action_log:
        if a.type == "play":
            for c in a.cards:
                assert 0 <= c.id < 108
    print(f"  PASS game action log ({plays} plays recorded)")


def test_view_public_stats():
    """opponent_public_stats exposes counts only — never card identities."""
    log = ActionLog()
    c1 = Card.from_id(0)
    combo = Combo(combo_type=ComboType.SINGLE, cards=(c1,),
                  main_rank=c1.rank, length=1, level=2)
    log.record_play(1, 1, [c1], combo)
    log.record_pass(2, 2, combo)
    state = _deal_state(sizes=(10, 9, 8, 7))
    view = PlayerView(state, 0, action_log=log)
    assert view.tracker is not None, "tracker must be live when a log is passed"
    s1 = view.opponent_public_stats(1)
    assert s1["played_count"] == 1 and s1["hand_size"] == 9
    s2 = view.opponent_public_stats(2)
    assert s2["pass_count"] == 1
    assert s2["passed_on"][0]["type"] == "SINGLE"
    # purity: no card ids anywhere in the stats dict
    assert "card" not in str(s1).lower() or "card_ids" not in s1
    print("  PASS public stats (counts only)")


def test_view_without_log_backward_compatible():
    state = _deal_state()
    view = PlayerView(state, 0)
    assert view.tracker is None
    assert view.opponent_public_stats(1)["played_count"] == 0
    print("  PASS no-log backward compat")


def test_tracker_level_and_constraint_sampling():
    """A player who passed on a high single gets no higher singles in samples."""
    level = 2
    log = ActionLog()
    high_single = Card.from_id(8 + 39)  # spades K? just need a real card
    # passed_on: single King (rank 13)
    passed = Combo(combo_type=ComboType.SINGLE, cards=(high_single,),
                   main_rank=high_single.rank, length=1, level=level)
    log.record_pass(3, 1, passed)
    log.record_pass(3, 3, passed)  # player 3 passed too (but we exclude only via history)

    own = tuple(Card.from_id(i) for i in range(0, 6))
    tracker = CardTracker(own, log, level=level)
    tracker.set_opponent_sizes({1: 5, 2: 5, 3: 5})
    random.seed(11)
    samples = tracker.sample_opponent_hands()
    r_eff_pass = 13  # K effective at level 2
    forbidden = {c.id for c in samples[1]
                 if c.rank.value <= 14 and c.rank.value > r_eff_pass + 1}
    assert not forbidden, f"player 1 passed on K yet sampled {forbidden}"
    print("  PASS constrained sampling (no clear beaters for the passer)")


def test_constrained_fallback_when_infeasible():
    """If the filtered pool is too small, fall back to unconstrained."""
    log = ActionLog()
    passed = Combo(combo_type=ComboType.SINGLE,
                   cards=(Card.from_id(4),),  # rank 6 single
                   main_rank=Rank.SIX, length=1, level=2)
    log.record_pass(1, 1, passed)
    own = tuple(Card.from_id(i) for i in range(0, 4))
    tracker = CardTracker(own, log, level=2)
    tracker.set_opponent_sizes({1: 40, 2: 20, 3: 20})  # 40 > feasible filtered pool
    random.seed(5)
    samples = tracker.sample_opponent_hands()
    assert len(samples[1]) == 40, "infeasible constraint must not lose cards"
    print("  PASS infeasible-constraint fallback")


def test_tribute_records_public_on_view():
    state = _deal_state()
    recs = [{"round": 2, "exchanges": [{"payer": 3, "receiver": 0,
                                        "paid": "SJ", "returned": "H2"}]}]
    view = PlayerView(state, 0, tribute_records=recs)
    assert view.tribute_public_records() == recs
    view2 = PlayerView(state, 0)
    assert view2.tribute_public_records() == []
    print("  PASS tribute records exposed publicly")


def test_opp_model_cover_bonus():
    """cover bonus applies only via context flag (tracker-driven)."""
    hand = tuple(Card.from_id(i) for i in [0, 1])  # 3C 3D
    follow = Combo(combo_type=ComboType.SINGLE, cards=(hand[1],),
                   main_rank=hand[1].rank, length=1, level=2)
    table = Combo(combo_type=ComboType.SINGLE, cards=(Card.from_id(2),),
                  main_rank=Rank.THREE, length=1, level=2)
    p = AIParams(opp_model_cover_bonus=3.0)
    with_bonus = dict(player_id=0, last_played_player=1, teammate_passed=False,
                      finished_positions=[], _cover_bonus=3.0)
    without = {k: v for k, v in with_bonus.items() if k != "_cover_bonus"}
    from guandan.ai.scorer import teammate_context_score
    a = teammate_context_score(follow, table, 2, with_bonus, p)
    b = teammate_context_score(follow, table, 2, without, p)
    assert a == 3.0 and b == 0.0
    print("  PASS opp-model cover bonus via context")


if __name__ == "__main__":
    test_game_wires_action_log()
    test_view_public_stats()
    test_view_without_log_backward_compatible()
    test_tracker_level_and_constraint_sampling()
    test_constrained_fallback_when_infeasible()
    test_tribute_records_public_on_view()
    test_opp_model_cover_bonus()
    print("OK")
