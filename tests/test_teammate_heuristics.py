"""Teammate-aware heuristic tests (stage 4.1).

Only public information is used (seat parity, trick history, finished
positions) — no opponent-hand access. All weights default to 0.0, so
"off" behaviour must be byte-identical to the previous scoring.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from guandan.card import Card, Rank, Suit  # noqa: E402
from guandan.combo import Combo, ComboType  # noqa: E402
from guandan.ai.params import AIParams  # noqa: E402
from guandan.ai.scorer import score_play, teammate_context_score  # noqa: E402


def _card(rank: Rank, suit: Suit = Suit.CLUBS, deck: int = 0) -> Card:
    for i in range(deck * 54, deck * 54 + 54):
        c = Card.from_id(i)
        if c.rank == rank and c.suit == suit:
            return c
    raise ValueError(f"no card {rank} {suit}")


def _single(card: Card) -> Combo:
    return Combo(combo_type=ComboType.SINGLE, cards=(card,),
                 main_rank=card.rank, length=1, level=2)


def _ids(cards):
    return [c.id for c in cards]


def test_zero_weights_identical_to_classic():
    """With all teammate weights 0 (default), scores equal the classic path."""
    hand = tuple(_card(r) for r in [Rank.THREE, Rank.K])
    low = _single(_card(Rank.THREE))
    high = _single(_card(Rank.K))
    ctx = {"player_id": 0, "last_played_player": 1, "teammate_passed": True,
           "finished_positions": [2]}
    for combo in (low, high):
        classic = score_play(combo, hand, tuple(), low, 2)
        with_ctx = score_play(combo, hand, tuple(), low, 2, context=ctx)
        assert classic == with_ctx, f"{combo}: {classic} != {with_ctx}"
    print("  PASS zero weights => identical scores")


def test_support_teammate_lead_prefers_low_follow():
    """Teammate owns the table: following low scores higher than covering high."""
    hand = tuple(_card(r) for r in [Rank.THREE, Rank.K])
    low = _single(_card(Rank.THREE))
    high = _single(_card(Rank.K))
    ctx = {"player_id": 0, "last_played_player": 2,  # teammate (seat 2) owns table
           "teammate_passed": False, "finished_positions": []}
    p = AIParams(support_teammate_lead=6.0)
    s_low = score_play(low, hand, tuple(), low, 2, p, context=ctx)
    s_high = score_play(high, hand, tuple(), low, 2, p, context=ctx)
    assert s_low > s_high, f"low {s_low} should beat high {s_high}"
    print(f"  PASS support_teammate_lead (low {s_low:.2f} > high {s_high:.2f})")


def test_support_teammate_lead_flips_preference():
    """Following: K and 3 score IDENTICALLY with the weight off (the classic
    scorer has no per-rank follow preference). Turning the weight on makes
    the low card strictly better — i.e. the weight CREATES the 送牌 bias."""
    hand = tuple(_card(r) for r in [Rank.THREE, Rank.K])
    low = _single(_card(Rank.THREE))
    high = _single(_card(Rank.K))
    ctx = {"player_id": 0, "last_played_player": 2,
           "teammate_passed": False, "finished_positions": []}
    off = AIParams()  # all teammate weights 0
    on = AIParams(support_teammate_lead=12.0)
    natural = score_play(high, hand, tuple(), low, 2, off, context=ctx) - \
        score_play(low, hand, tuple(), low, 2, off, context=ctx)
    flipped = score_play(high, hand, tuple(), low, 2, on, context=ctx) - \
        score_play(low, hand, tuple(), low, 2, on, context=ctx)
    assert natural == 0.0, "no follow preference without the weight"
    assert flipped < 0, "with the weight on, 3 must outscore K"
    print(f"  PASS follow bias created (0.00 -> {flipped:.2f})")


def test_block_opponent_lead_bonus():
    """Opponent owns the table and the partner passed: covering gets a bonus."""
    hand = tuple(_card(r) for r in [Rank.TEN])
    cover = _single(_card(Rank.TEN))
    opp_ctx = {"player_id": 0, "last_played_player": 1,  # opponent owns table
               "teammate_passed": True, "finished_positions": []}
    mate_ctx = {"player_id": 0, "last_played_player": 2,  # teammate owns table
                "teammate_passed": True, "finished_positions": []}
    p = AIParams(block_opponent_lead=5.0)
    s_opp = score_play(cover, hand, tuple(), cover, 2, p, context=opp_ctx)
    s_mate = score_play(cover, hand, tuple(), cover, 2, p, context=mate_ctx)
    assert s_opp > s_mate, "covering an opponent should outscore overriding a partner"
    print(f"  PASS block_opponent_lead (opp {s_opp:.2f} > mate {s_mate:.2f})")


def test_push_teammate_endgame_bonus():
    """Partner finished: playing more cards scores proportionally higher."""
    hand3 = tuple(_card(r) for r in [Rank.THREE, Rank.FOUR, Rank.FIVE])
    single = _single(_card(Rank.THREE))
    ctx_fin = {"player_id": 0, "last_played_player": -1,
               "teammate_passed": False, "finished_positions": [2]}
    ctx_not = {"player_id": 0, "last_played_player": -1,
               "teammate_passed": False, "finished_positions": []}
    p = AIParams(push_teammate_endgame=4.0)
    s_fin = score_play(single, hand3, hand3[1:], None, 2, p, context=ctx_fin)
    s_not = score_play(single, hand3, hand3[1:], None, 2, p, context=ctx_not)
    assert s_fin - s_not == pytest_approx(p.push_teammate_endgame)
    print(f"  PASS push_teammate_endgame (+{s_fin - s_not:.2f})")


def test_bombs_get_half_endgame_bonus():
    """Bombs are precious: endgame push applies at half weight."""
    bomb_cards = [_card(Rank.SIX, Suit.CLUBS), _card(Rank.SIX, Suit.DIAMONDS),
                  _card(Rank.SIX, Suit.HEARTS), _card(Rank.SIX, Suit.SPADES)]
    bomb = Combo(combo_type=ComboType.NORMAL_BOMB, cards=tuple(bomb_cards),
                 main_rank=Rank.SIX, length=4, level=2)
    hand = tuple(bomb_cards) + tuple(_card(r) for r in [Rank.THREE])
    ctx = {"player_id": 0, "last_played_player": -1,
           "teammate_passed": False, "finished_positions": [2]}
    p = AIParams(push_teammate_endgame=4.0)
    got = teammate_context_score(bomb, None, 2, ctx, p)
    assert got == pytest_approx(4.0 * 4 * 0.5), f"bomb bonus {got}"
    print(f"  PASS bomb half-weight ({got:.2f})")


def test_no_context_neutral():
    """context=None (or empty) returns exactly 0 adjustment."""
    hand = tuple(_card(r) for r in [Rank.THREE])
    single = _single(_card(Rank.THREE))
    p = AIParams(support_teammate_lead=9.0, block_opponent_lead=9.0,
                 push_teammate_endgame=9.0)
    assert teammate_context_score(single, None, 2, None, p) == 0.0
    assert teammate_context_score(single, None, 2, {}, p) == 0.0
    a = score_play(single, hand, tuple(), None, 2, p, context=None)
    b = score_play(single, hand, tuple(), None, 2, p, context={})
    assert a == b
    print("  PASS no-context neutral")


def pytest_approx(expected, tol=1e-6):
    class _A:
        def __eq__(self, other):
            return abs(other - expected) < tol
    return _A()


if __name__ == "__main__":
    test_zero_weights_identical_to_classic()
    test_support_teammate_lead_prefers_low_follow()
    test_support_teammate_lead_flips_preference()
    test_block_opponent_lead_bonus()
    test_push_teammate_endgame_bonus()
    test_bombs_get_half_endgame_bonus()
    test_no_context_neutral()
    print("OK")
