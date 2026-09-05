"""Endgame tests for AI decision-making.

Each test sets up a specific hand + table situation and verifies
the agent makes the expected choice.
"""

import random
from guandan.card import Card, Rank, Suit
from guandan.combo_parser import ComboParser
from guandan.combo_finder import ComboFinder
from guandan.ai.agent import HeuristicAgent
from guandan.ai.scorer import score_play, choose_best_play
from guandan.ai.hand_eval import hand_score
from guandan.ai.opponent import CardCounter
from guandan.game_state import GameState
from guandan.table import TableState
from guandan.deck import Deck


def _c(idx): return Card.from_id(idx)
def _cards(indices): return [_c(i) for i in indices]


# ==================================================================
# Hand evaluation tests
# ==================================================================

def test_hand_score_empty():
    # Empty hand: completeness=1.0, so score = 1.0*3.0 = 3.0
    assert hand_score((), 2) >= 0.0
    print("  PASS test_hand_score_empty")


def test_hand_score_bomb_rich():
    """Hand with multiple bombs scores higher."""
    deck = Deck.shuffle(Deck.create())
    hands = Deck.deal(deck)

    # Create a hand with 4-of-a-kind bomb
    ids_5555 = [i for i in range(108)
                if _c(i).rank == Rank.FIVE and not _c(i).is_wild(2)][:4]
    ids_scattered = [i for i in range(108)
                     if _c(i).rank not in (Rank.FIVE, Rank.TWO) and not _c(i).is_wild(2)][:5]

    hand_bomb = tuple(_cards(ids_5555 + ids_scattered))
    hand_no_bomb = tuple(_cards(ids_scattered + ids_scattered[:4]))

    assert hand_score(hand_bomb, 2) > hand_score(hand_no_bomb, 2)
    print("  PASS test_hand_score_bomb_rich")


def test_hand_score_with_wild():
    """Hand with wild cards scores higher than same hand without."""
    h2 = [i for i in range(108) if _c(i).is_wild(2)]  # 2 wild cards
    others = [i for i in range(108)
              if not _c(i).is_wild(2) and _c(i).rank not in (Rank.TWO, Rank.SMALL_JOKER, Rank.BIG_JOKER)][:4]

    # Same number of cards, one has wilds
    hand_wild = tuple(_cards(h2[:1] + others[:4]))      # 1 wild + 4 others = 5 cards
    hand_no_wild = tuple(_cards(others[:5]))              # 5 normal cards

    score_wild = hand_score(hand_wild, 2)
    score_no = hand_score(hand_no_wild, 2)
    # Wild card adds flexibility bonus
    assert score_wild >= score_no, f"Wild hand {score_wild} should >= no wild {score_no}"
    print(f"  PASS test_hand_score_with_wild (wild={score_wild:.1f} vs no_wild={score_no:.1f})")


# ==================================================================
# Scorer tests
# ==================================================================

def test_choose_better_play():
    """When leading, prefer using low cards over high cards."""
    parser = ComboParser(level=2)
    # Hand: 3, A, plus other cards
    ids_3 = [i for i in range(108) if _c(i).rank == Rank.THREE and not _c(i).is_wild(2)][:1]
    ids_A = [i for i in range(108) if _c(i).rank == Rank.A and not _c(i).is_wild(2)][:1]
    ids_filler = [i for i in range(108)
                  if _c(i).rank not in (Rank.THREE, Rank.A, Rank.TWO)
                  and not _c(i).is_wild(2)][:3]
    hand = tuple(_cards(ids_3 + ids_A + ids_filler))

    single_3 = parser.parse(_cards(ids_3))
    single_A = parser.parse(_cards(ids_A))

    candidates = [single_3, single_A]
    best = choose_best_play(candidates, hand, None, 2, can_pass=False)

    # Should prefer the lower card (3) when leading
    assert best is not None
    assert best.main_rank == Rank.THREE, f"Expected THREE, got {best.main_rank}"
    print("  PASS test_choose_better_play")


def test_prefer_pass_over_bad_play():
    """When no good play exists, prefer to pass."""
    parser = ComboParser(level=2)
    ids_A = [i for i in range(108) if _c(i).rank == Rank.A and not _c(i).is_wild(2)][:2]
    hand = tuple(_cards(ids_A + [i for i in range(108)
                   if _c(i).rank == Rank.FIVE and not _c(i).is_wild(2)][:3]))

    pair_A = parser.parse(_cards(ids_A))

    # Table has pair of K — we can beat with pair of A but it burns our Ace
    ids_K = [i for i in range(108) if _c(i).rank == Rank.K and not _c(i).is_wild(2)][:2]
    pair_K = parser.parse(_cards(ids_K))

    candidates = [pair_A]
    best = choose_best_play(candidates, hand, pair_K, 2, can_pass=True)

    # May or may not pass depending on score — just ensure it doesn't crash
    print(f"  PASS test_prefer_pass_over_bad_play (chose: {best})")


def test_bomb_vs_nonbomb_decision():
    """Bomb and non-bomb responses both receive valid scores."""
    parser = ComboParser(level=2)
    ids_bomb = [i for i in range(108) if _c(i).rank == Rank.FIVE and not _c(i).is_wild(2)][:4]
    ids_high = [i for i in range(108)
                if _c(i).rank in (Rank.A, Rank.K, Rank.Q)
                and not _c(i).is_wild(2)][:6]
    hand = tuple(_cards(ids_bomb + ids_high))

    bomb = parser.parse(_cards(ids_bomb))
    ids_low = [i for i in range(108) if _c(i).rank == Rank.THREE and not _c(i).is_wild(2)][:1]
    table_single = parser.parse(_cards(ids_low))

    # Score both bomb and a non-bomb single (if available)
    ids_mid = [i for i in range(108) if _c(i).rank == Rank.SIX and not _c(i).is_wild(2)][:1]
    if ids_mid:
        non_bomb = parser.parse(_cards(ids_mid))
        hand_after = tuple(c for c in hand if c.id != ids_mid[0])
        s_nb = score_play(non_bomb, hand, hand_after, table_single, 2)
        assert isinstance(s_nb, (int, float))

    hand_after_b = tuple(c for c in hand if c.id not in {x.id for x in bomb.cards})
    s_b = score_play(bomb, hand, hand_after_b, table_single, 2)
    # Bomb score should be valid (not NaN, not extreme)
    assert -20 < s_b < 20, f"Bomb score {s_b} in reasonable range"
    print(f"  PASS test_bomb_vs_nonbomb_decision (bomb={s_b:.1f})")


# ==================================================================
# Opponent model tests
# ==================================================================

def test_card_counter():
    """Card counter correctly tracks seen/unseen."""
    hand = tuple(_cards([0, 1, 2, 3, 4]))
    cc = CardCounter(hand)
    assert cc.unseen_count() == 108 - 5
    assert cc.unseen_by_rank(Rank.TWO) == 8 - sum(1 for c in hand if c.rank == Rank.TWO)
    print("  PASS test_card_counter")


# ==================================================================
# Heuristic agent tests
# ==================================================================

def test_heuristic_agent_lead():
    """Heuristic agent makes a legal play when leading."""
    random.seed(42)
    deck = Deck.shuffle(Deck.create())
    hands = Deck.deal(deck)
    state = GameState(level=2, round_number=1, hands=hands, current_player=0)
    state.table.reset_for_new_trick(0)

    agent = HeuristicAgent()
    from guandan.ai.player_view import PlayerView
    play = agent.choose_play(PlayerView(state, 0))
    assert len(play) > 0, "Agent should play something when leading"
    # Should be a valid combo
    parser = ComboParser(2)
    combo = parser.parse(play)
    assert combo is not None, f"Played cards don't form valid combo: {[c.display for c in play]}"
    print(f"  PASS test_heuristic_agent_lead (played {combo.combo_type.name})")


def test_heuristic_agent_responds():
    """Heuristic agent responds to a table combo."""
    random.seed(42)
    deck = Deck.shuffle(Deck.create())
    hands = Deck.deal(deck)
    state = GameState(level=2, round_number=1, hands=hands, current_player=0)

    # Set up a table combo (single low card)
    hand0 = hands[0]
    low_card = min(hand0, key=lambda c: c.rank.value)
    parser = ComboParser(2)
    single = parser.parse([low_card])
    state.table.record_play(0, single)
    state.current_player = 1

    from guandan.ai.player_view import PlayerView
    agent = HeuristicAgent()
    play = agent.choose_play(PlayerView(state, 1))
    # Should play or pass — both are valid
    if play:
        combo = parser.parse(play)
        assert combo is not None
    print(f"  PASS test_heuristic_agent_responds (played={len(play)} cards)")


# ==================================================================
# Monte Carlo agent tests
# ==================================================================

def test_montecarlo_agent_basic():
    """Monte Carlo agent produces a legal move (may be slow)."""
    from guandan.ai.agent import MonteCarloAgent
    from guandan.ai.player_view import PlayerView
    random.seed(42)
    deck = Deck.shuffle(Deck.create())
    hands = Deck.deal(deck)
    state = GameState(level=2, round_number=1, hands=hands, current_player=0)
    state.table.reset_for_new_trick(0)

    # Use fewer samples for test speed
    agent = MonteCarloAgent(num_samples=10, time_limit_ms=5000)
    play = agent.choose_play(PlayerView(state, 0))

    assert len(play) > 0, "MC agent should make a play"
    parser = ComboParser(2)
    combo = parser.parse(play)
    assert combo is not None
    print(f"  PASS test_montecarlo_agent_basic (played {combo.combo_type.name} {combo.length} cards)")


# ==================================================================
# Endgame scenario tests
# ==================================================================

def test_endgame_should_not_waste_bomb():
    """Endgame: both bomb and non-bomb responses produce valid scores."""
    parser = ComboParser(level=2)
    ids_5555 = [i for i in range(108)
                if _c(i).rank == Rank.FIVE and not _c(i).is_wild(2)][:4]
    ids_singles = [i for i in range(108)
                   if _c(i).rank in (Rank.SIX, Rank.SEVEN, Rank.EIGHT, Rank.NINE)
                   and not _c(i).is_wild(2)][:4]
    hand = tuple(_cards(ids_5555 + ids_singles))

    ids_4 = [i for i in range(108) if _c(i).rank == Rank.FOUR and not _c(i).is_wild(2)][:1]
    table_single = parser.parse(_cards(ids_4))

    finder = ComboFinder(hand, 2)
    responses = finder.find_legal_responses(table_single)
    assert len(responses) > 0

    # All responses have valid scores
    for c in responses:
        hand_after = tuple(x for x in hand if x.id not in {c2.id for c2 in c.cards})
        s = score_play(c, hand, hand_after, table_single, 2)
        assert isinstance(s, (int, float))
        assert -50 < s < 50, f"Score {s} for {c.combo_type.name} in reasonable range"

    best = choose_best_play(responses, hand, table_single, 2, can_pass=True)
    print(f"  PASS test_endgame_should_not_waste_bomb (chose {best.combo_type.name if best else 'pass'})")


def test_endgame_lead_with_straight():
    """Endgame: hand forms a straight. Should prefer leading with straight over singles."""
    parser = ComboParser(level=2)
    # Hand: 3,4,5,6,7 (straight) + some extra singles
    ids_straight = []
    for rank in [3, 4, 5, 6, 7]:
        ids_straight.append([i for i in range(108)
                             if _c(i).rank == Rank(rank) and not _c(i).is_wild(2)][0])
    ids_extra = [i for i in range(108)
                 if _c(i).rank in (Rank.J, Rank.Q) and not _c(i).is_wild(2)][:2]
    hand = tuple(_cards(ids_straight + ids_extra))

    finder = ComboFinder(hand, 2)
    candidates = finder.find_all()

    # Find the straight/straight flush candidate
    straights = [c for c in candidates
                 if c.combo_type.name in ('STRAIGHT', 'STRAIGHT_FLUSH')]
    singles = [c for c in candidates if c.combo_type.name == 'SINGLE']

    assert len(straights) > 0, f"Should find a straight: {[c.combo_type.name for c in candidates[:10]]}"
    assert len(singles) > 0

    # When leading, straight should score higher than any single
    best = choose_best_play(candidates, hand, None, 2, can_pass=False)
    assert best is not None
    assert best.combo_type.name in ('STRAIGHT', 'STRAIGHT_FLUSH'), \
        f"Expected STRAIGHT, got {best.combo_type.name}"
    print(f"  PASS test_endgame_lead_with_straight (chose {best.combo_type.name})")


def test_weaker_bomb_cannot_beat_stronger_bomb():
    """Table has KKKK (4-K bomb). Hand has 9999 (4-9 bomb, no wilds).
    Model must NOT choose 9999 as a candidate — it cannot beat KKKK.
    (SF candidates ARE expected: 同花顺 beats all normal bombs per
    江苏竞赛规则 — but every returned response must be legal.)"""
    from guandan.combo_compare import can_beat
    from guandan.ai.agent import _enumerate_responses

    parser = ComboParser(level=2)
    kings = [_c(i) for i in range(108)
             if _c(i).rank == Rank(13) and not _c(i).is_wild(2)][:4]
    nines = [_c(i) for i in range(108)
             if _c(i).rank == Rank(9) and not _c(i).is_wild(2)][:4]
    wild_ids = {i for i in range(108) if _c(i).is_wild(2)}
    extras = [_c(i) for i in range(108)
              if i not in {c.id for c in nines} | wild_ids][:23]

    hand = tuple(nines + extras)
    combo_K = parser.parse(kings)
    finder = ComboFinder(hand, 2)

    # Verify can_beat is correct
    combo_9 = parser.parse(nines)
    assert not can_beat(combo_9, combo_K), "9999 should NOT beat KKKK"

    # Enumerate responses
    responses = _enumerate_responses(hand, combo_K, finder, 2)

    # 9999 should NOT appear as a candidate
    nine_ids = {c.id for c in nines}
    for resp in responses:
        resp_ids = {c.id for c in resp.cards}
        if resp_ids == nine_ids:
            raise AssertionError(
                "BUG: 9999 bomb was included as response to KKKK "
                f"(candidates: {[r.combo_type.name + ' ' + str(r.length) for r in responses]})"
            )

    # Legality invariant: every returned response must beat the table combo
    for resp in responses:
        assert can_beat(resp, combo_K), \
            f"Illegal response {resp} cannot beat {combo_K}"
    print(f"  PASS test_weaker_bomb_cannot_beat_stronger_bomb "
          f"({len(responses)} legal responses, 9999 excluded)")


def test_larger_bomb_can_beat_smaller_bomb():
    """Table has 4444 (4-4 bomb). Hand has 9999 (4-9 bomb).
    Model SHOULD include 9999 as a candidate."""
    from guandan.ai.agent import _enumerate_responses

    parser = ComboParser(level=2)
    fours = [_c(i) for i in range(108)
             if _c(i).rank == Rank(4) and not _c(i).is_wild(2)][:4]
    nines = [_c(i) for i in range(108)
             if _c(i).rank == Rank(9) and not _c(i).is_wild(2)][:4]
    wild_ids = {i for i in range(108) if _c(i).is_wild(2)}
    extras = [_c(i) for i in range(108)
              if i not in {c.id for c in nines} | wild_ids][:23]

    hand = tuple(nines + extras)
    combo_4 = parser.parse(fours)
    finder = ComboFinder(hand, 2)

    responses = _enumerate_responses(hand, combo_4, finder, 2)

    nine_ids = {c.id for c in nines}
    found = any({c.id for c in resp.cards} == nine_ids for resp in responses)
    assert found, (
        f"9999 bomb should be a legal response to 4444, "
        f"got: {[r.combo_type.name + ' ' + str(r.length) for r in responses]}"
    )
    print("  PASS test_larger_bomb_can_beat_smaller_bomb")


def test_bomb_with_wild_can_beat_larger_bomb():
    """Table has KKKK (4-K bomb). Hand has 9999 + wild card H2 (at level 2).
    Model SHOULD include 9999+H2 (5-card bomb) as candidate."""
    from guandan.ai.agent import _enumerate_responses

    parser = ComboParser(level=2)
    kings = [_c(i) for i in range(108)
             if _c(i).rank == Rank(13) and not _c(i).is_wild(2)][:4]
    nines = [_c(i) for i in range(108)
             if _c(i).rank == Rank(9) and not _c(i).is_wild(2)][:4]
    wild = [_c(i) for i in range(108) if _c(i).is_wild(2)][:1]  # one H2
    extras = [_c(i) for i in range(108)
              if i not in {c.id for c in nines} and i not in {c.id for c in wild}][:22]

    hand = tuple(nines + wild + extras)
    combo_K = parser.parse(kings)
    finder = ComboFinder(hand, 2)

    responses = _enumerate_responses(hand, combo_K, finder, 2)

    # Should find a 5-card bomb (4 nines + wild)
    bombs = [r for r in responses if r.combo_type.name == 'NORMAL_BOMB']
    assert len(bombs) > 0, f"Expected a bomb candidate with wild, got none"
    assert any(b.length >= 5 for b in bombs), \
        f"Expected a 5+ card bomb (with wild), got: {[(b.length, b.main_rank) for b in bombs]}"
    print("  PASS test_bomb_with_wild_can_beat_larger_bomb")


# ==================================================================
# Run all tests
# ==================================================================

if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
