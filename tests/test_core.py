"""Tests for Guandan game engine core components."""

import random
from guandan.card import Card, Rank, Suit
from guandan.combo import Combo, ComboType
from guandan.combo_parser import ComboParser
from guandan.combo_compare import can_beat
from guandan.combo_finder import ComboFinder
from guandan.deck import Deck
from guandan.rules import RulesEngine, PlayLegality
from guandan.table import TableState
from guandan.game import Game
from guandan.score import calculate_result, advance_level, is_game_won
from guandan.ai.agent import RandomAgent, FirstPlayAgent


# ---------------------------------------------------------------------------
# Card tests
# ---------------------------------------------------------------------------

def test_card_encoding():
    """Card IDs 0-107 map correctly."""
    assert Card.from_id(0) == Card(id=0, rank=Rank.TWO, suit=Suit.CLUBS, deck=0)
    assert Card.from_id(52).rank == Rank.SMALL_JOKER
    assert Card.from_id(53).rank == Rank.BIG_JOKER
    assert Card.from_id(54) == Card(id=54, rank=Rank.TWO, suit=Suit.CLUBS, deck=1)
    assert Card.from_id(107).rank == Rank.BIG_JOKER
    print("  PASS test_card_encoding")


def test_wild_detection():
    """Heart-suit level card is wild."""
    # At level 2, only H2 is wild
    wild_ids = [i for i in range(108) if Card.from_id(i).is_wild(2)]
    assert len(wild_ids) == 2
    assert all(Card.from_id(i).suit == Suit.HEARTS for i in wild_ids)
    assert all(Card.from_id(i).rank == Rank.TWO for i in wild_ids)

    # At level 5, only H5 is wild
    wild_ids_5 = [i for i in range(108) if Card.from_id(i).is_wild(5)]
    assert len(wild_ids_5) == 2
    assert all(Card.from_id(i).suit == Suit.HEARTS for i in wild_ids_5)
    assert all(Card.from_id(i).rank == Rank.FIVE for i in wild_ids_5)
    print("  PASS test_wild_detection")


# ---------------------------------------------------------------------------
# Combo parser tests
# ---------------------------------------------------------------------------

def _c(idx): return Card.from_id(idx)
def _cards(indices): return [_c(i) for i in indices]
def _find_cards(rank, suit=None, count=1, level=2):
    """Find card IDs matching criteria."""
    result = []
    for i in range(108):
        c = _c(i)
        if c.rank == rank and not c.is_wild(level):
            if suit is None or c.suit == suit:
                result.append(i)
                if len(result) >= count:
                    break
    return result


def test_parser_singles():
    parser = ComboParser(level=2)
    combo = parser.parse(_cards([0]))
    assert combo.combo_type == ComboType.SINGLE
    assert combo.main_rank == Rank.TWO
    assert combo.length == 1
    print("  PASS test_parser_singles")


def test_parser_pairs():
    parser = ComboParser(level=2)
    # Natural pair
    ids = _find_cards(Rank.FIVE, count=2)
    combo = parser.parse(_cards(ids))
    assert combo.combo_type == ComboType.PAIR
    assert combo.main_rank == Rank.FIVE

    # Wild pair: C2 + H2(wild) at level 2
    c2 = _find_cards(Rank.TWO, suit=Suit.CLUBS, count=1)
    h2 = [i for i in range(108) if _c(i).is_wild(2)][0]
    combo = parser.parse(_cards(c2 + [h2]))
    assert combo.combo_type == ComboType.PAIR
    assert combo.main_rank == Rank.TWO
    assert len(combo.wild_indices) == 1
    print("  PASS test_parser_pairs")


def test_parser_triples():
    parser = ComboParser(level=2)
    ids = _find_cards(Rank.NINE, count=3)
    combo = parser.parse(_cards(ids))
    assert combo.combo_type == ComboType.TRIPLE
    assert combo.main_rank == Rank.NINE
    print("  PASS test_parser_triples")


def test_parser_triple_side():
    parser = ComboParser(level=2)
    # Triple+Single: now illegal in Guandan (掼蛋不允许三带一)
    triple_ids = _find_cards(Rank.FIVE, count=3)
    single_ids = _find_cards(Rank.EIGHT, count=1)
    combo = parser.parse(_cards(triple_ids + single_ids))
    assert combo is None, "三带一 should be illegal"

    # Triple+Pair: 3 of rank K + 2 of rank 3
    triple_ids = _find_cards(Rank.K, count=3)
    pair_ids = _find_cards(Rank.THREE, count=2)
    combo = parser.parse(_cards(triple_ids + pair_ids))
    assert combo.combo_type == ComboType.TRIPLE_PAIR
    assert combo.main_rank == Rank.K
    print("  PASS test_parser_triple_side")


def test_parser_straight():
    parser = ComboParser(level=2)
    # Straight 3,4,5,6,7 (different suits to avoid straight flush)
    ids = []
    for i, rank in enumerate([3, 4, 5, 6, 7]):
        suit = Suit((i % 4))  # different suits
        cid = _find_cards(Rank(rank), suit=suit, count=1)[0]
        ids.append(cid)
    combo = parser.parse(_cards(ids))
    assert combo.combo_type == ComboType.STRAIGHT
    assert combo.main_rank == Rank.SEVEN
    assert combo.length == 5

    # Straight with wild: 3,4,wild,6,7 (wild fills 5)
    ids2 = []
    for rank in [3, 4, 6, 7]:
        ids2.append(_find_cards(Rank(rank), count=1)[0])
    h2 = [i for i in range(108) if _c(i).is_wild(2)][0]
    ids2.append(h2)
    combo = parser.parse(_cards(ids2))
    assert combo.combo_type == ComboType.STRAIGHT
    assert combo.main_rank == Rank.SEVEN
    assert len(combo.wild_indices) == 1
    print("  PASS test_parser_straight")


def test_parser_consecutive_pairs():
    parser = ComboParser(level=2)
    # 3,3,4,4,5,5
    ids = []
    for rank in [3, 3, 4, 4, 5, 5]:
        existing = sum(1 for x in ids if _c(x).rank == Rank(rank))
        cid = _find_cards(Rank(rank), count=existing + 1)[existing]
        ids.append(cid)
    combo = parser.parse(_cards(ids))
    assert combo.combo_type == ComboType.CONSECUTIVE_PAIRS
    assert combo.main_rank == Rank.FIVE
    assert combo.length == 6
    print("  PASS test_parser_consecutive_pairs")


def test_parser_consecutive_triples():
    parser = ComboParser(level=2)
    # 3,3,3,4,4,4
    ids = []
    for rank in [3, 3, 3, 4, 4, 4]:
        existing = sum(1 for x in ids if _c(x).rank == Rank(rank))
        cid = _find_cards(Rank(rank), count=existing + 1)[existing]
        ids.append(cid)
    combo = parser.parse(_cards(ids))
    assert combo.combo_type == ComboType.CONSECUTIVE_TRIPLES
    assert combo.main_rank == Rank.FOUR
    assert combo.length == 6
    print("  PASS test_parser_consecutive_triples")


def test_parser_bomb():
    parser = ComboParser(level=2)
    # 4 of a kind
    ids = _find_cards(Rank.TEN, count=4)
    combo = parser.parse(_cards(ids))
    assert combo.combo_type == ComboType.NORMAL_BOMB
    assert combo.main_rank == Rank.TEN
    assert combo.length == 4

    # 5 of a kind with wild
    ids = _find_cards(Rank.SEVEN, count=3)
    h2 = [i for i in range(108) if _c(i).is_wild(2)][0]
    combo = parser.parse(_cards(ids + [h2]))
    assert combo.combo_type == ComboType.NORMAL_BOMB
    assert combo.main_rank == Rank.SEVEN
    assert combo.length == 4
    print("  PASS test_parser_bomb")


def test_parser_rocket():
    parser = ComboParser(level=2)
    # Exactly 2 Big Jokers + 2 Small Jokers = 天王炸
    bj_all = [i for i in range(108) if _c(i).rank == Rank.BIG_JOKER]
    sj_all = [i for i in range(108) if _c(i).rank == Rank.SMALL_JOKER]
    assert len(bj_all) == 2 and len(sj_all) == 2
    combo = parser.parse(_cards(bj_all + sj_all))
    assert combo.combo_type == ComboType.ROCKET
    assert combo.length == 4

    # 1 Big + 1 Small is NOT a rocket anymore — it's invalid
    combo = parser.parse(_cards([bj_all[0], sj_all[0]]))
    assert combo is None, f"1BJ+1SJ should be invalid, got {combo}"

    # 2 Big Jokers is still a pair
    combo = parser.parse(_cards(bj_all))
    assert combo.combo_type == ComboType.PAIR
    print("  PASS test_parser_rocket")


def test_parser_straight_flush():
    parser = ComboParser(level=2)
    # 5 cards same suit, consecutive
    ids = []
    for rank in [3, 4, 5, 6, 7]:
        cid = _find_cards(Rank(rank), suit=Suit.CLUBS, count=1)[0]
        ids.append(cid)
    combo = parser.parse(_cards(ids))
    assert combo.combo_type == ComboType.STRAIGHT_FLUSH
    assert combo.suit == Suit.CLUBS
    print("  PASS test_parser_straight_flush")


def test_finder_response_to_sf_returns_legal_type():
    """pick_response on an SF target must return a higher SF — never a plain
    straight (that was the masked-coercion bug)."""
    from guandan.combo_finder import ComboFinder
    target = _sf(Suit.CLUBS, 3)  # C3-C7 on the table
    # Hand: higher hearts SF H5-H9 + junk
    hand_ids = []
    for rv in range(5, 10):
        hand_ids.append(_find_cards(Rank(rv), suit=Suit.HEARTS, count=1)[0])
    hand_ids += _find_cards(Rank.TWO, count=3)  # junk (2s can't join straights)
    hand = tuple(_cards(hand_ids))
    finder = ComboFinder(hand, 2)
    resp = finder.pick_response(target)
    assert resp is not None
    assert resp.combo_type == ComboType.STRAIGHT_FLUSH, \
        f"Expected SF response, got {resp.combo_type.name}"
    assert resp.main_rank.value > target.main_rank.value
    print("  PASS test_finder_response_to_sf_returns_legal_type")


def test_parser_invalid():
    parser = ComboParser(level=2)
    # Different ranks without valid combo
    combo = parser.parse(_cards([0, 1]))  # C2 + C3
    assert combo is None
    # Empty
    assert parser.parse([]) is None
    print("  PASS test_parser_invalid")


# ---------------------------------------------------------------------------
# Combo comparison tests
# ---------------------------------------------------------------------------

def test_compare_same_type():
    parser = ComboParser(level=2)
    a = parser.parse(_cards(_find_cards(Rank.FIVE, count=2)))
    b = parser.parse(_cards(_find_cards(Rank.THREE, count=2)))
    assert can_beat(a, b)   # pair 5 beats pair 3
    assert not can_beat(b, a)  # pair 3 doesn't beat pair 5
    assert not can_beat(a, a)  # same rank can't beat
    print("  PASS test_compare_same_type")


def test_compare_bomb_vs_nonbomb():
    parser = ComboParser(level=2)
    bomb = parser.parse(_cards(_find_cards(Rank.FOUR, count=4)))
    pair = parser.parse(_cards(_find_cards(Rank.A, count=2)))
    assert can_beat(bomb, pair)  # bomb beats any non-bomb
    assert not can_beat(pair, bomb)  # non-bomb doesn't beat bomb
    print("  PASS test_compare_bomb_vs_nonbomb")


def test_compare_bomb_vs_bomb():
    parser = ComboParser(level=2)
    bomb4_3 = parser.parse(_cards(_find_cards(Rank.THREE, count=4)))
    bomb5_3 = parser.parse(_cards(_find_cards(Rank.THREE, count=5)))
    bomb4_5 = parser.parse(_cards(_find_cards(Rank.FIVE, count=4)))
    # Rocket = 4 jokers (2 Big + 2 Small)
    bj_all = [i for i in range(108) if _c(i).rank == Rank.BIG_JOKER]
    sj_all = [i for i in range(108) if _c(i).rank == Rank.SMALL_JOKER]
    rocket = parser.parse(_cards(bj_all + sj_all))

    assert can_beat(bomb5_3, bomb4_3)  # bigger bomb (5>4)
    assert not can_beat(bomb4_3, bomb5_3)  # smaller doesn't beat bigger
    assert can_beat(bomb4_5, bomb4_3)  # same size, higher rank wins
    assert can_beat(rocket, bomb5_3)  # rocket beats any bomb
    assert not can_beat(bomb5_3, rocket)  # nothing beats rocket
    print("  PASS test_compare_bomb_vs_bomb")


def test_compare_different_nonbomb():
    parser = ComboParser(level=2)
    pair = parser.parse(_cards(_find_cards(Rank.FIVE, count=2)))
    single = parser.parse(_cards(_find_cards(Rank.A, count=1)))
    assert not can_beat(single, pair)  # different non-bomb types can't beat
    assert not can_beat(pair, single)
    print("  PASS test_compare_different_nonbomb")


# ---------------------------------------------------------------------------
# Straight flush hierarchy tests (江苏竞赛规则: 王炸＞同花顺＞炸弹＞一般牌型)
# ---------------------------------------------------------------------------

def _sf(suit: Suit, start_rank: int) -> Combo:
    """Build a 5-card same-suit run starting at start_rank."""
    parser = ComboParser(level=2)
    ids = []
    for rv in range(start_rank, start_rank + 5):
        ids.append(_find_cards(Rank(rv), suit=suit, count=1)[0])
    return parser.parse(_cards(ids))


def test_sf_beats_normal_bomb():
    """同花顺 beats ALL normal bombs regardless of bomb size/rank."""
    parser = ComboParser(level=2)
    sf = _sf(Suit.CLUBS, 3)  # C3-C7 straight flush
    bomb8_k = parser.parse(_cards(_find_cards(Rank.K, count=8)))  # 8-card bomb
    bomb4_a = parser.parse(_cards(_find_cards(Rank.A, count=4)))
    assert can_beat(sf, bomb8_k), "SF must beat an 8-card bomb"
    assert can_beat(sf, bomb4_a), "SF must beat a 4-card bomb"
    print("  PASS test_sf_beats_normal_bomb")


def test_normal_bomb_cannot_beat_sf():
    parser = ComboParser(level=2)
    sf = _sf(Suit.CLUBS, 3)
    bomb4_a = parser.parse(_cards(_find_cards(Rank.A, count=4)))
    bomb8_3 = parser.parse(_cards(_find_cards(Rank.THREE, count=8)))
    assert not can_beat(bomb4_a, sf)
    assert not can_beat(bomb8_3, sf)
    print("  PASS test_normal_bomb_cannot_beat_sf")


def test_sf_beats_plain_straight_and_sequences():
    """SF beats any non-bomb: plain straight (even longer/higher), 连对."""
    parser = ComboParser(level=2)
    sf = _sf(Suit.CLUBS, 3)  # C3-C7
    # longer, higher plain straight 8-K (6 cards, mixed suits → not SF)
    ids = [_find_cards(Rank(8), suit=Suit.HEARTS, count=1)[0],
           _find_cards(Rank(9), suit=Suit.DIAMONDS, count=1)[0],
           _find_cards(Rank(10), suit=Suit.CLUBS, count=1)[0],
           _find_cards(Rank(11), suit=Suit.SPADES, count=1)[0],
           _find_cards(Rank(12), suit=Suit.HEARTS, count=1)[0],
           _find_cards(Rank(13), suit=Suit.DIAMONDS, count=1)[0]]
    long_straight = parser.parse(_cards(ids))
    assert long_straight.combo_type == ComboType.STRAIGHT
    assert long_straight.length == 6
    assert can_beat(sf, long_straight)
    # 连对 3-8 (6 pairs) — higher than SF main rank 7
    cp_ids = []
    for rv in range(3, 9):
        cp_ids.extend(_find_cards(Rank(rv), count=2))
    cp = parser.parse(_cards(cp_ids))
    assert cp.combo_type == ComboType.CONSECUTIVE_PAIRS
    assert can_beat(sf, cp)
    print("  PASS test_sf_beats_plain_straight_and_sequences")


def test_sf_vs_sf_higher_rank_wins():
    sf_3 = _sf(Suit.CLUBS, 3)   # C3-C7
    sf_5 = _sf(Suit.HEARTS, 5)  # H5-H9
    assert can_beat(sf_5, sf_3)
    assert not can_beat(sf_3, sf_5)
    print("  PASS test_sf_vs_sf_higher_rank_wins")


def test_only_rocket_and_higher_sf_beat_sf():
    parser = ComboParser(level=2)
    sf = _sf(Suit.CLUBS, 3)
    bj_all = [i for i in range(108) if _c(i).rank == Rank.BIG_JOKER]
    sj_all = [i for i in range(108) if _c(i).rank == Rank.SMALL_JOKER]
    rocket = parser.parse(_cards(bj_all + sj_all))
    assert can_beat(rocket, sf), "rocket beats SF"
    assert not can_beat(sf, rocket)
    # lower SF cannot beat higher SF
    sf_high = _sf(Suit.SPADES, 8)  # S8-S12(Q)
    sf_low = _sf(Suit.DIAMONDS, 3)
    assert not can_beat(sf_low, sf_high)
    print("  PASS test_only_rocket_and_higher_sf_beat_sf")


# ---------------------------------------------------------------------------
# Deck tests
# ---------------------------------------------------------------------------

def test_deck():
    deck = Deck.create()
    assert len(deck) == 108
    assert len(set(c.id for c in deck)) == 108  # all unique

    shuffled = Deck.shuffle(deck)
    assert len(shuffled) == 108
    # Not the same order (extremely unlikely to fail)
    assert [c.id for c in shuffled] != [c.id for c in deck]

    hands = Deck.deal(shuffled)
    assert len(hands) == 4
    assert all(len(h) == 27 for h in hands)
    # All cards distributed
    all_ids = set()
    for h in hands:
        all_ids.update(c.id for c in h)
    assert len(all_ids) == 108
    print("  PASS test_deck")


# ---------------------------------------------------------------------------
# Rules validation tests
# ---------------------------------------------------------------------------

def test_rules_leader():
    rules = RulesEngine(level=2)
    deck = Deck.shuffle(Deck.create())
    hands = Deck.deal(deck)
    table = TableState(trick_leader=0)

    # Leader plays a single: legal
    result = rules.validate_play(
        cards=[hands[0][0]],
        hand=hands[0],
        table_state=table,
        player_id=0,
        finished_positions=[],
    )
    assert result.is_legal

    # Leader passes: illegal
    result = rules.validate_play(
        cards=[],
        hand=hands[0],
        table_state=table,
        player_id=0,
        finished_positions=[],
    )
    assert not result.is_legal
    assert result.reason == PlayLegality.CANNOT_PASS_AS_LEADER
    print("  PASS test_rules_leader")


def test_rules_card_not_in_hand():
    rules = RulesEngine(level=2)
    deck = Deck.shuffle(Deck.create())
    hands = Deck.deal(deck)
    table = TableState(trick_leader=0)

    # Play a card from another player's hand
    result = rules.validate_play(
        cards=[hands[1][0]],
        hand=hands[0],
        table_state=table,
        player_id=0,
        finished_positions=[],
    )
    assert not result.is_legal
    assert result.reason == PlayLegality.CARDS_NOT_IN_HAND
    print("  PASS test_rules_card_not_in_hand")


# ---------------------------------------------------------------------------
# Scoring tests
# ---------------------------------------------------------------------------

def test_scoring():
    # Team 0: players 0,2. Team 1: players 1,3.
    # 头游=0, 二游=2 (same team) → +3
    r = calculate_result([0, 2, 1])
    assert r.winning_team == 0
    assert r.level_change == 3
    assert r.positions == [0, 2, 1, 3]

    # 头游=1, 二游=0 (diff team), 三游=3 (same as 1) → +2
    r = calculate_result([1, 0, 3])
    assert r.winning_team == 1
    assert r.level_change == 2

    # 头游=0, 二游=1 (diff), 三游=3 (diff from 0) → +1 (partner is last)
    r = calculate_result([0, 1, 3])
    assert r.winning_team == 0
    assert r.level_change == 1
    print("  PASS test_scoring")


def test_level_progression():
    assert advance_level(2, 3) == 5
    assert advance_level(13, 2) == 14  # capped at A
    assert advance_level(14, 1) == 14  # stays at A
    assert is_game_won(14) is True
    assert is_game_won(13) is False
    print("  PASS test_level_progression")


# ---------------------------------------------------------------------------
# Full game integration test
# ---------------------------------------------------------------------------

def test_full_game():
    random.seed(42)
    agents = [RandomAgent() for _ in range(4)]
    game = Game(agents, level=2)
    result = game.play_game()
    assert result.rounds_played > 0
    assert result.winning_team in (0, 1)
    assert result.final_levels[result.winning_team] >= 14
    assert len(result.round_results) == result.rounds_played
    print(f"  PASS test_full_game ({result.rounds_played} rounds)")


def test_game_no_rule_violations():
    """Run multiple games with random agents and verify no rule violations."""
    for seed in range(10):
        random.seed(seed)
        agents = [RandomAgent() for _ in range(4)]
        game = Game(agents, level=2)
        result = game.play_game()
        assert result.rounds_played > 0
        assert result.winning_team in (0, 1)
    print("  PASS test_game_no_rule_violations (10 games)")


# ---------------------------------------------------------------------------
# ComboFinder tests
# ---------------------------------------------------------------------------

def test_finder_pick_lead():
    """ComboFinder.pick_lead always returns a valid combo from a non-empty hand."""
    random.seed(42)
    deck = Deck.shuffle(Deck.create())
    hands = Deck.deal(deck)
    finder = ComboFinder(hands[0], level=2)
    for _ in range(20):
        combo = finder.pick_lead()
        assert combo is not None
        assert 1 <= combo.length <= 8
    print("  PASS test_finder_pick_lead")


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------

def test_edge_straight_no_two():
    """A straight cannot contain rank 2 as a natural card."""
    parser = ComboParser(level=2)
    # Try: 2,3,4,5,6 — rank 2 forbidden in straight
    ids = []
    for rank in [2, 3, 4, 5, 6]:
        ids.append(_find_cards(Rank(rank), count=1)[0])
    combo = parser.parse(_cards(ids))
    assert combo is None or combo.combo_type != ComboType.STRAIGHT
    print("  PASS test_edge_straight_no_two")


def test_edge_straight_with_wild_fills_two():
    """Wild card (native rank=2 at level 2) fills a gap, is NOT treated as rank 2."""
    parser = ComboParser(level=2)
    # 3,4,wild,6,7: wild fills 5 (not treated as 2)
    ids = []
    for rank in [3, 4, 6, 7]:
        ids.append(_find_cards(Rank(rank), count=1)[0])
    h2 = [i for i in range(108) if _c(i).is_wild(2)][0]
    ids.append(h2)
    combo = parser.parse(_cards(ids))
    assert combo.combo_type == ComboType.STRAIGHT
    assert combo.main_rank == Rank.SEVEN
    print("  PASS test_edge_straight_with_wild_fills_two")


def test_edge_all_wild_pair():
    """2 wild cards form a pair at native rank (level)."""
    parser = ComboParser(level=5)
    h5_ids = [i for i in range(108) if _c(i).is_wild(5)]
    assert len(h5_ids) == 2
    combo = parser.parse(_cards(h5_ids))
    assert combo.combo_type == ComboType.PAIR
    assert combo.main_rank == Rank.FIVE
    print("  PASS test_edge_all_wild_pair")


def test_edge_wild_cannot_sub_joker():
    """Wild cards cannot substitute for jokers."""
    parser = ComboParser(level=2)
    h2 = [i for i in range(108) if _c(i).is_wild(2)][0]
    bj_all = [i for i in range(108) if _c(i).rank == Rank.BIG_JOKER]
    sj_all = [i for i in range(108) if _c(i).rank == Rank.SMALL_JOKER]

    # Wild + joker: invalid (wild can't substitute for joker)
    combo = parser.parse(_cards([h2, bj_all[0]]))
    assert combo is None, f"Wild+BJ should be invalid, got {combo}"
    combo = parser.parse(_cards([h2, sj_all[0]]))
    assert combo is None, f"Wild+SJ should be invalid, got {combo}"

    # 2 jokers of same rank: valid pair
    combo = parser.parse(_cards([bj_all[0], bj_all[1]]))
    assert combo is not None and combo.combo_type == ComboType.PAIR
    print("  PASS test_edge_wild_cannot_sub_joker")


def test_edge_triple_pair_wild():
    """Triple+Pair formed with wild cards."""
    parser = ComboParser(level=2)
    # 2 normals of rank 8 + 1 wild = triple 8
    # 2 normals of rank 3 = pair 3
    triple_ids = _find_cards(Rank.EIGHT, count=2)
    pair_ids = _find_cards(Rank.THREE, count=2)
    h2 = [i for i in range(108) if _c(i).is_wild(2)][0]
    combo = parser.parse(_cards(triple_ids + [h2] + pair_ids))
    assert combo is not None
    assert combo.combo_type in (ComboType.TRIPLE_PAIR,)
    print("  PASS test_edge_triple_pair_wild")


def test_edge_bomb_size_limit():
    """Max bomb is 8 cards (2 decks * 4 suits)."""
    parser = ComboParser(level=2)
    # 8 of a kind (all 8 copies from 2 decks)
    ids = _find_cards(Rank.THREE, count=8)
    assert len(ids) == 8
    combo = parser.parse(_cards(ids))
    assert combo.combo_type == ComboType.NORMAL_BOMB
    assert combo.length == 8
    print("  PASS test_edge_bomb_size_limit")


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    print("Running Guandan core tests...\n")
if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
