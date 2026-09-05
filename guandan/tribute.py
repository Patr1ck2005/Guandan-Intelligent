"""Tribute (进贡/还贡/抗贡) mechanics — 江苏竞赛规则.

After each completed round (except a game-ending round), the losing team
pays tribute to the winning team before the next hand's first trick:

- 双下 (winning team took 1st AND 2nd): both losers each pay one card
  (to the 1st and 2nd place winners respectively) — "双贡".
- 单下 (otherwise): only the worst-placed loser pays one card to the 1st.
- 贡牌: the payer's largest card by effective rank, EXCLUDING level-rank
  cards (级牌不可进贡). Jokers may be paid.
- 抗贡: a payer holding a pair of big jokers (一对大王) refuses tribute.
  In 双贡, if EITHER payer can resist, BOTH tributes are cancelled.
- 还贡: each receiver returns one card of their choice. v1 auto-strategy:
  return the smallest card by effective rank.
- 先出权 (next-round leader):
    * tribute paid, 单下 → the payer leads;
    * tribute paid, 双下 → the payer of the LARGER tribute card leads;
    * 抗贡 (any) → the round's 1st-place player (头游) leads.

The exchange happens AFTER dealing and BEFORE the first trick, so hand
sizes return to 27 for everyone. Tribute cards are public information
(recorded in the exchange log) but are NOT plays — they never enter
``played_cards``.

All selection helpers are deterministic (no RNG) so seeded self-play
stays reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import constants
from .card import Card, Rank, effective_rank


Hands = Tuple[Tuple[Card, ...], Tuple[Card, ...],
              Tuple[Card, ...], Tuple[Card, ...]]


@dataclass
class TributeExchange:
    """One payer/receiver card exchange."""

    payer: int
    receiver: int
    paid_card: Optional[Card] = None
    returned_card: Optional[Card] = None  # None when 抗贡

    def to_dict(self) -> dict:
        return {
            "payer": self.payer,
            "receiver": self.receiver,
            "paid": self.paid_card.display if self.paid_card else None,
            "returned": self.returned_card.display if self.returned_card else None,
        }


@dataclass
class TributePlan:
    """The tribute arrangement for the upcoming round."""

    anti_tribute: bool = False         # 一对大王抗贡 → all tributes cancelled
    exchanges: List[TributeExchange] = field(default_factory=list)
    leader: int = -1                   # who plays first this round
    round_level: int = 2               # 级牌 for the upcoming round

    @property
    def payers(self) -> List[int]:
        return [e.payer for e in self.exchanges]

    def to_dict(self) -> dict:
        return {
            "anti_tribute": self.anti_tribute,
            "leader": self.leader,
            "round_level": self.round_level,
            "exchanges": [e.to_dict() for e in self.exchanges],
        }


def largest_tribute_card(hand: Tuple[Card, ...], level: int) -> Optional[Card]:
    """The payer's largest card by effective rank, excluding 级牌.

    Level-rank cards (all suits of ``level``) may not be paid as tribute,
    which also excludes the wild card (红桃级牌) automatically. Ties are
    broken deterministically by suit value.
    """
    best: Optional[Card] = None
    best_key = (-1, -1)
    for c in hand:
        if c.rank.value == level:
            continue  # 级牌不可进贡
        key = (effective_rank(c.rank, level), c.suit.value)
        if key > best_key:
            best, best_key = c, key
    return best


def smallest_return_card(hand: Tuple[Card, ...], level: int) -> Optional[Card]:
    """v1 return strategy: the smallest card by effective rank (deterministic)."""
    best: Optional[Card] = None
    best_key = (1 << 30, 1 << 30)
    for c in hand:
        key = (effective_rank(c.rank, level), c.suit.value)
        if key < best_key:
            best, best_key = c, key
    return best


def has_big_joker_pair(hand: Tuple[Card, ...]) -> bool:
    """抗贡 condition: the payer holds BOTH big jokers (一对大王)."""
    return sum(1 for c in hand if c.rank == Rank.BIG_JOKER) >= 2


def _payer_pairs(positions: List[int]) -> Tuple[List[Tuple[int, int]], bool]:
    """Who pays whom. Returns ([(payer, receiver), ...], is_double)."""
    first, second, _third, _fourth = positions
    first_team = constants.TEAMS[first]
    losers = [p for p in positions if constants.TEAMS[p] != first_team]
    if not losers:
        return [], False

    double = constants.TEAMS[second] == first_team
    if double:
        # Both losers pay: worse-placed loser → 头游, other → 二游.
        # losers[] is in finish order (positions order), so losers[0] is
        # the better-placed one → pairs to 头游, losers[1] → 二游.
        return [(losers[0], first), (losers[1], second)], True
    worst = max(losers, key=positions.index)
    return [(worst, first)], False


def plan_tribute(positions: List[int], team_levels: List[int],
                 round_level: int, hands: Optional[Hands] = None) -> TributePlan:
    """Plan the tribute for the upcoming round from the finished round result.

    Args:
        positions: finish order of the completed round ``[头游, 二游, 三游, 末游]``.
        team_levels: [team0, team1] levels (informational; 级牌 = round_level).
        round_level: 级牌 for the upcoming round (头游队伍的级别).
        hands: dealt hands for the upcoming round — required to evaluate
            抗贡 (一对大王). When omitted, 抗贡 cannot be detected and is
            assumed False.

    ``leader`` is -1 for a pending 双下 plan until :func:`execute_tribute`
    resolves which payer produced the larger tribute.
    """
    plan = TributePlan(round_level=round_level)
    if len(positions) != 4:
        return plan

    pairs, _is_double = _payer_pairs(positions)
    if not pairs:
        return plan

    # 抗贡: any payer holding 一对大王 cancels ALL tributes.
    if hands is not None:
        if any(has_big_joker_pair(hands[payer]) for payer, _ in pairs):
            plan.anti_tribute = True
            plan.leader = positions[0]  # 抗贡 → 头游先出
            return plan

    plan.exchanges = [TributeExchange(payer=payer, receiver=receiver)
                      for payer, receiver in pairs]
    if len(plan.exchanges) == 1:
        plan.leader = plan.exchanges[0].payer  # 单下 → 进贡者先出
    # 双下 leader resolved in execute_tribute after cards are picked.
    return plan


def execute_tribute(hands: Hands, plan: TributePlan, level: int,
                    defer_return_for: Optional[int] = None) -> Tuple[Hands, TributePlan]:
    """Perform the tribute exchange on the dealt hands. Deterministic.

    Step 1: each payer hands over their largest non-级牌 card.
    Step 2: each receiver returns their smallest card back to that payer.
    Step 3: 先出权 — 单下 payer leads; 双下 → larger tribute's payer leads.

    Args:
        defer_return_for: player id whose receiver-side return is deferred
            (human 还贡 via UI). The tribute card still lands in their hand;
            the exchange's ``returned_card`` stays None until the UI submits
            the chosen card.

    On 抗贡 (``plan.anti_tribute``) hands are returned unchanged.
    """
    if plan.anti_tribute or not plan.exchanges:
        if plan.leader < 0:
            plan.leader = 0
        return hands, plan

    hands_list: List[Tuple[Card, ...]] = [tuple(h) for h in hands]

    # Step 1: pay the largest non-级牌 card (remove from payer).
    for ex in plan.exchanges:
        card = largest_tribute_card(hands_list[ex.payer], level)
        if card is None:
            # Degenerate: payer holds only 级牌 — impossible with 27 cards
            # (at most 8 of any rank). Skip the exchange defensively.
            continue
        ex.paid_card = card
        hands_list[ex.payer] = tuple(
            c for c in hands_list[ex.payer] if c.id != card.id)

    # Step 2: receivers return their smallest card (remove → add to payer).
    for ex in plan.exchanges:
        if ex.paid_card is None:
            continue
        if defer_return_for is not None and ex.receiver == defer_return_for:
            continue  # human will pick the return card in the UI
        ret = smallest_return_card(hands_list[ex.receiver], level)
        if ret is None:
            continue
        ex.returned_card = ret
        hands_list[ex.receiver] = tuple(
            c for c in hands_list[ex.receiver] if c.id != ret.id)
        hands_list[ex.payer] = hands_list[ex.payer] + (ret,)

    # Paid cards land with the receivers.
    for ex in plan.exchanges:
        if ex.paid_card is not None:
            hands_list[ex.receiver] = hands_list[ex.receiver] + (ex.paid_card,)

    # Step 3: 先出权.
    if len(plan.exchanges) == 1:
        plan.leader = plan.exchanges[0].payer
    else:
        # 双下: larger tribute card's payer leads (deterministic tie-break:
        # the payer paired to the 头游 wins the tie).
        best = max(plan.exchanges,
                   key=lambda ex: (effective_rank(ex.paid_card.rank, level)
                                   if ex.paid_card else -1,))
        plan.leader = best.payer

    return tuple(hands_list), plan
