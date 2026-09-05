"""Tests for the tribute (进贡/还贡/抗贡) module."""

from guandan.card import Card, Rank
from guandan.tribute import (
    TributeExchange,
    TributePlan,
    execute_tribute,
    has_big_joker_pair,
    largest_tribute_card,
    plan_tribute,
    smallest_return_card,
)


def _cards_of_rank(rank, count):
    """Deterministic card pick: first `count` ids of a rank."""
    out = []
    for i in range(108):
        c = Card.from_id(i)
        if c.rank == Rank(rank):
            out.append(c)
            if len(out) == count:
                break
    return out


def _deal_with_hands(hands_spec, filler_max_rank=12):
    """Build a 4-tuple of hands from {player: [card, ...]} filling the rest
    with filler cards so every hand has exactly 27 cards.

    Filling order: players named in ``hands_spec`` get low-rank filler
    (3..filler_max_rank) first, so injected jokers/kings/aces stay the
    largest cards in THEIR hands; everyone else gets the leftovers.
    """
    used = set()
    for cards in hands_spec.values():
        used.update(c.id for c in cards)
    all_unused = [c for c in (Card.from_id(i) for i in range(108))
                  if c.id not in used]
    low = [c for c in all_unused if c.rank.value <= filler_max_rank]
    high = [c for c in all_unused if c.rank.value > filler_max_rank]

    hands = [None] * 4
    # Specified players first — low filler only.
    for p, cards in hands_spec.items():
        base = list(cards)
        while len(base) < 27:
            base.append(low.pop(0))
        hands[p] = tuple(base[:27])
    # Everyone else: leftovers (low remainder, then high).
    rest = low + high
    for p in range(4):
        if hands[p] is None:
            hands[p] = tuple(rest[:27])
            del rest[:27]
    assert all(h is not None for h in hands)
    return tuple(hands)


# ---------------------------------------------------------------------------
# plan_tribute
# ---------------------------------------------------------------------------

def test_plan_single_down_tribute():
    """头游+三游 same team (单下): worst loser (末游) pays the 头游."""
    # 头游=1, 二游=0, 三游=3 (team1 wins: 1,3), 末游=2 (team0)
    plan = plan_tribute([1, 0, 3, 2], [2, 2], 2)
    assert len(plan.exchanges) == 1
    ex = plan.exchanges[0]
    assert ex.payer == 2 and ex.receiver == 1
    assert plan.anti_tribute is False
    assert plan.leader == 2  # 进贡者先出


def test_plan_single_down_partner_last():
    """头游+末游 same team: losers are 二游/三游, worst loser (三游) pays."""
    # 头游=1, 二游=0, 三游=3 (team1), 末游=2 (team0) → team1 wins 单下
    plan = plan_tribute([1, 0, 3, 2], [2, 2], 2)
    assert len(plan.exchanges) == 1
    ex = plan.exchanges[0]
    assert ex.payer == 2 and ex.receiver == 1


def test_plan_double_down_tribute():
    """头游+二游 same team (双下): both losers pay.

    Convention: 三游 pays the 头游, 末游 pays the 二游."""
    # 头游=0, 二游=2 (team0 wins), 三游=1, 末游=3 (team1 loses both)
    plan = plan_tribute([0, 2, 1, 3], [2, 2], 2)
    assert len(plan.exchanges) == 2
    assert {(e.payer, e.receiver) for e in plan.exchanges} == {(1, 0), (3, 2)}
    # 双下 leader resolved after execute; plan-stage leader is -1
    assert plan.leader == -1


def test_plan_anti_tribute_cancels_all():
    """双下 + 任一进贡者持一对大王 → 全部免贡, 头游先出."""
    hands = _deal_with_hands({
        3: _cards_of_rank(Rank.BIG_JOKER.value, 2),   # payer 3
    })
    # 头游=0, 二游=2 (team0), losers: 1, 3
    plan = plan_tribute([0, 2, 1, 3], [2, 2], 2, hands=hands)
    assert plan.anti_tribute is True
    assert plan.exchanges == []
    assert plan.leader == 0  # 头游先出


def test_plan_anti_tribute_needs_hands():
    """Without hands, 抗贡 cannot be detected."""
    plan = plan_tribute([0, 2, 1, 3], [2, 2], 2)
    assert plan.anti_tribute is False


def test_plan_no_tribute_positions_incomplete():
    plan = plan_tribute([1, 0], [2, 2], 2)
    assert plan.exchanges == []


def test_has_big_joker_pair():
    two_bj = _cards_of_rank(Rank.BIG_JOKER.value, 2)
    one_bj = _cards_of_rank(Rank.BIG_JOKER.value, 1)
    sj = _cards_of_rank(Rank.SMALL_JOKER.value, 1)
    assert has_big_joker_pair(tuple(two_bj))
    assert not has_big_joker_pair(tuple(one_bj))
    assert not has_big_joker_pair(tuple(one_bj + sj))


# ---------------------------------------------------------------------------
# Card selection helpers
# ---------------------------------------------------------------------------

def test_largest_tribute_card_excludes_level_cards():
    """级牌不可进贡 — largest card selection skips all level-rank cards."""
    level = 5
    big_j = _cards_of_rank(Rank.BIG_JOKER.value, 1)
    fives = _cards_of_rank(5, 3)
    small = _cards_of_rank(3, 1)
    hand = tuple(big_j + fives + small)
    got = largest_tribute_card(hand, level)
    assert got.rank == Rank.BIG_JOKER  # joker pays, 级牌 skipped

    # Only 级牌 + low cards: largest payable is the low card
    hand2 = tuple(fives + small)
    got2 = largest_tribute_card(hand2, level)
    assert got2.rank == Rank(3)  # 5s are 级牌, so 3 pays despite being smaller


def test_largest_tribute_card_none_when_only_level_cards():
    level = 7
    sevens = _cards_of_rank(7, 8)
    assert largest_tribute_card(tuple(sevens), level) is None


def test_smallest_return_card():
    level = 2
    hand = tuple(_cards_of_rank(13, 1) + _cards_of_rank(3, 1)
                 + _cards_of_rank(9, 1))
    got = smallest_return_card(hand, level)
    assert got.rank == Rank(3)


# ---------------------------------------------------------------------------
# execute_tribute
# ---------------------------------------------------------------------------

def test_execute_single_exchange():
    level = 2
    payer_cards = _cards_of_rank(13, 2)          # K K
    bj = _cards_of_rank(Rank.BIG_JOKER.value, 1)
    hands = _deal_with_hands({3: payer_cards + bj})
    plan = TributePlan(round_level=level)
    plan.exchanges = [TributeExchange(payer=3, receiver=1)]
    plan.leader = 3
    new_hands, plan = execute_tribute(hands, plan, level)
    # Payer gave the big joker (largest), received smallest back
    ex = plan.exchanges[0]
    assert ex.paid_card.rank == Rank.BIG_JOKER
    assert ex.returned_card is not None
    # Hand sizes unchanged
    assert all(len(h) == 27 for h in new_hands)
    # Paid card moved to receiver
    assert any(c.id == ex.paid_card.id for c in new_hands[1])
    assert not any(c.id == ex.paid_card.id for c in new_hands[3])
    # Returned card moved to payer
    assert any(c.id == ex.returned_card.id for c in new_hands[3])
    assert not any(c.id == ex.returned_card.id for c in new_hands[1])
    # Still 108 distinct cards overall
    ids = [c.id for h in new_hands for c in h]
    assert len(set(ids)) == 108 and len(ids) == 108


def test_execute_double_down_leader_is_larger_tribute():
    level = 2
    # Payer 1 pays a K, payer 3 pays an A → payer 3 leads
    p1 = _cards_of_rank(13, 1)
    p3 = _cards_of_rank(14, 1)
    hands = _deal_with_hands({1: p1, 3: p3})
    plan = TributePlan(round_level=level)
    plan.exchanges = [TributeExchange(payer=1, receiver=0),
                      TributeExchange(payer=3, receiver=2)]
    new_hands, plan = execute_tribute(hands, plan, level)
    assert plan.leader == 3
    assert plan.exchanges[0].paid_card.rank == Rank(13)
    assert plan.exchanges[1].paid_card.rank == Rank(14)
    assert all(len(h) == 27 for h in new_hands)


def test_execute_anti_tribute_keeps_hands():
    hands = _deal_with_hands({})
    plan = TributePlan(round_level=2, anti_tribute=True, leader=1)
    new_hands, plan2 = execute_tribute(hands, plan, 2)
    assert new_hands == hands
    assert plan2.leader == 1
