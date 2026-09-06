"""Combo parser: determine what valid combination a set of played cards forms.

Uses a cascade detector pattern — tries each combo type in descending priority
order. The first match wins. Each detector handles wild card resolution.

Constraints:
  - At most 2 wild cards exist in the entire deck (one heart-level per deck).
  - Wild cards (红心级牌/万能牌) can substitute for any rank 2-A, not jokers.
  - In bombs, wilds add to the card count of the bomb's rank.
"""

from __future__ import annotations

from collections import Counter
from typing import List, Optional, Tuple

from .card import Card, Rank, Suit, effective_rank
from .combo import Combo, ComboType


class ComboParser:
    """Parse a set of played cards into the single highest-priority valid combo."""

    def __init__(self, level: int):
        self.level = level

    def parse(self, cards: List[Card]) -> Optional[Combo]:
        """Return the resolved Combo, or None if cards don't form a valid combo."""
        if not cards:
            return None

        n = len(cards)
        wilds = [c for c in cards if c.is_wild(self.level)]
        normals = [c for c in cards if not c.is_wild(self.level)]
        wild_count = len(wilds)

        # Detect cascade, highest priority first
        for detector in self._detectors(n, normals, wilds, wild_count):
            result = detector(normals, wilds, wild_count)
            if result is not None:
                return result
        return None

    def _detectors(self, n: int, normals: List[Card], wilds: List[Card], wild_count: int):
        """Yield detector methods applicable for the given card count."""
        if n == 4:
            yield self._try_rocket
        if n == 5:
            yield self._try_straight_flush
        if 4 <= n <= 8:
            yield self._try_normal_bomb
        if n >= 6 and n % 3 == 0:
            yield self._try_consecutive_triples
        if n >= 6 and n % 2 == 0:
            yield self._try_consecutive_pairs
        if n == 5:
            yield self._try_straight
        if n == 5:
            yield self._try_triple_pair
        if n == 3:
            yield self._try_triple
        if n == 2:
            yield self._try_pair
        if n == 1:
            yield self._try_single

    # ------------------------------------------------------------------
    # Detectors
    # ------------------------------------------------------------------

    def _try_rocket(self, normals: List[Card], wilds: List[Card], wild_count: int) -> Optional[Combo]:
        """Rocket / 天王炸: exactly 4 jokers (2 Big + 2 Small). No wilds allowed."""
        all_cards = normals + wilds
        if len(all_cards) != 4:
            return None
        big = sum(1 for c in all_cards if c.rank == Rank.BIG_JOKER)
        small = sum(1 for c in all_cards if c.rank == Rank.SMALL_JOKER)
        if big == 2 and small == 2:
            return Combo(
                combo_type=ComboType.ROCKET,
                cards=tuple(sorted(all_cards, key=lambda c: c.rank)),
                main_rank=Rank.BIG_JOKER,
                length=4,
                level=self.level,
            )
        return None

    def _try_straight_flush(self, normals: List[Card], wilds: List[Card], wild_count: int) -> Optional[Combo]:
        """Straight flush (同花顺): exactly 5 cards, same suit, consecutive."""
        all_cards = normals + wilds
        if len(all_cards) != 5:
            return None

        # Wilds are always hearts — if wilds present, suit must be hearts
        normal_suits = {c.suit for c in normals if not c.is_joker}
        if len(normal_suits) > 1:
            return None
        if wild_count > 0 and normal_suits and next(iter(normal_suits)) != Suit.HEARTS:
            return None
        suit = Suit.HEARTS if wild_count > 0 else (normal_suits.pop() if normal_suits else Suit.HEARTS)

        # No jokers in straight flush
        if any(c.is_joker for c in normals):
            return None

        # No rank 2 naturally in straights (wilds substitute for other ranks)
        normal_ranks = [c.rank for c in normals]
        if Rank.TWO in normal_ranks:
            return None

        return self._resolve_straight(normals, wilds, wild_count, 5, suit)

    def _try_normal_bomb(self, normals: List[Card], wilds: List[Card], wild_count: int) -> Optional[Combo]:
        """Normal bomb (普通炸弹): 4-8 cards all of the same rank."""
        total = len(normals) + len(wilds)
        if not (4 <= total <= 8):
            return None

        # All normals must share the same rank; jokers are not normal ranks (but can't be in bomb anyway)
        if normals:
            normal_ranks = {c.rank for c in normals}
            if len(normal_ranks) > 1:
                return None
            rank = normals[0].rank
        else:
            # All wild — native rank is the level rank
            rank = Rank(self.level)

        # Wilds just add to bomb size
        wild_indices = tuple(
            i for i, c in enumerate(normals + wilds)
            if c.is_wild(self.level)
        )
        all_cards = normals + wilds
        return Combo(
            combo_type=ComboType.NORMAL_BOMB,
            cards=tuple(all_cards),
            main_rank=rank,
            length=total,
            wild_indices=wild_indices,
            level=self.level,
        )

    def _try_consecutive_triples(self, normals: List[Card], wilds: List[Card], wild_count: int) -> Optional[Combo]:
        """Consecutive triples (钢板/飞机): 2+ consecutive ranks, 3 of each."""
        total = len(normals) + len(wilds)
        if total < 6 or total % 3 != 0:
            return None
        num_triples = total // 3
        return self._resolve_multi(
            normals, wilds, wild_count, num_triples, 3, ComboType.CONSECUTIVE_TRIPLES
        )

    def _try_consecutive_pairs(self, normals: List[Card], wilds: List[Card], wild_count: int) -> Optional[Combo]:
        """Consecutive pairs (连对/板凳): 3+ consecutive ranks, 2 of each."""
        total = len(normals) + len(wilds)
        if total < 6 or total % 2 != 0:
            return None
        num_pairs = total // 2
        return self._resolve_multi(
            normals, wilds, wild_count, num_pairs, 2, ComboType.CONSECUTIVE_PAIRS
        )

    def _try_straight(self, normals: List[Card], wilds: List[Card], wild_count: int) -> Optional[Combo]:
        """Straight (顺子): exactly 5 consecutive singles, no 2s or jokers.

        官方规则 (江苏竞赛规则): 顺子固定 5 张 — a 6+ card run of singles is
        NOT a legal combo (it fails to parse → the play is illegal).
        """
        total = len(normals) + len(wilds)
        if total != 5:
            return None
        if len(normals) == 0:
            return None  # need at least some normal cards to anchor

        # No jokers in straights
        if any(c.is_joker for c in normals):
            return None
        # Rank 2 (TWO) cannot be in straights (as a natural card)
        if any(c.rank == Rank.TWO for c in normals):
            return None
        # No duplicate normal ranks
        normal_rank_counter = Counter(c.rank for c in normals)
        if any(v > 1 for v in normal_rank_counter.values()):
            return None

        return self._resolve_straight(normals, wilds, wild_count, total, None)

    def _try_triple_pair(self, normals: List[Card], wilds: List[Card], wild_count: int) -> Optional[Combo]:
        """Triple + Pair (三带二): 5 cards, one triple + one pair of different ranks."""
        return self._try_triple_side(normals, wilds, wild_count, side_size=2, combo_type=ComboType.TRIPLE_PAIR)

    def _try_triple(self, normals: List[Card], wilds: List[Card], wild_count: int) -> Optional[Combo]:
        """Triple (三张): exactly 3 cards of same rank."""
        if len(normals) + len(wilds) != 3:
            return None
        return self._resolve_fixed(normals, wilds, wild_count, 3, ComboType.TRIPLE)

    def _try_pair(self, normals: List[Card], wilds: List[Card], wild_count: int) -> Optional[Combo]:
        """Pair (对子): exactly 2 cards of same rank."""
        return self._resolve_fixed(normals, wilds, wild_count, 2, ComboType.PAIR)

    def _try_single(self, normals: List[Card], wilds: List[Card], wild_count: int) -> Optional[Combo]:
        """Single (单张): exactly 1 card."""
        card = (normals + wilds)[0]
        rank = card.rank
        wild_indices = (0,) if wild_count > 0 else ()
        return Combo(
            combo_type=ComboType.SINGLE,
            cards=(card,),
            main_rank=rank,
            length=1,
            wild_indices=wild_indices,
            level=self.level,
        )

    # ------------------------------------------------------------------
    # Shared resolution helpers
    # ------------------------------------------------------------------

    def _resolve_fixed(
        self, normals: List[Card], wilds: List[Card], wild_count: int,
        required: int, combo_type: ComboType,
    ) -> Optional[Combo]:
        """Resolve combos with a fixed number of same-rank cards (pair=2, triple=3).

        All normals must be of the same rank. Wilds fill the deficit.
        Wilds cannot substitute for jokers.
        """
        total = len(normals) + len(wilds)
        if total != required:
            return None

        if normals:
            normal_ranks = {c.rank for c in normals}
            if len(normal_ranks) > 1:
                return None
            main_rank = normals[0].rank
            # Wilds cannot substitute for jokers
            if wild_count > 0 and main_rank >= Rank.SMALL_JOKER:
                return None
        else:
            # All wild cards: use their native level rank
            main_rank = Rank(self.level)

        wild_indices = tuple(
            i for i, c in enumerate(normals + wilds)
            if c.is_wild(self.level)
        )
        return Combo(
            combo_type=combo_type,
            cards=tuple(normals + wilds),
            main_rank=main_rank,
            length=required,
            wild_indices=wild_indices,
            level=self.level,
        )

    def _try_triple_side(
        self, normals: List[Card], wilds: List[Card], wild_count: int,
        side_size: int, combo_type: ComboType,
    ) -> Optional[Combo]:
        """Resolve triple+pair (side=2).

        The triple and the side pair must have different ranks.
        Wilds can contribute to either component.
        """
        expected_total = 3 + side_size
        all_cards = normals + wilds
        if len(all_cards) != expected_total:
            return None

        normal_ranks = Counter(c.rank for c in normals)

        # Try each normal rank as the triple's rank
        for triple_rank, triple_normal_count in normal_ranks.items():
            needed_wilds_for_triple = max(0, 3 - triple_normal_count)
            if needed_wilds_for_triple > wild_count:
                continue

            remaining_wilds = wild_count - needed_wilds_for_triple
            # Remaining normals are those NOT of the triple rank
            remaining_normals = [c for c in normals if c.rank != triple_rank]
            remaining_count = len(remaining_normals) + remaining_wilds

            if remaining_count != side_size:
                continue

            # Need a pair of rank different from triple
            side_normal_ranks = Counter(c.rank for c in remaining_normals)
            if len(side_normal_ranks) > 1:
                continue
            if side_normal_ranks:
                side_rank = next(iter(side_normal_ranks))
                side_normal_count = side_normal_ranks[side_rank]
                needed_for_side = max(0, 2 - side_normal_count)
                if needed_for_side != remaining_wilds:
                    continue
            else:
                # All remaining are wilds (2 wilds for the pair)
                side_rank = Rank(self.level)

            if side_rank == triple_rank:
                continue

            # Build the combo
            wild_indices = tuple(
                i for i, c in enumerate(all_cards)
                if c.is_wild(self.level)
            )
            return Combo(
                combo_type=combo_type,
                cards=tuple(all_cards),
                main_rank=triple_rank,
                length=expected_total,
                secondary_rank=side_rank,
                side_type='pair',
                wild_indices=wild_indices,
                level=self.level,
            )

        # Try wilds forming the triple entirely (no normal triple rank)
        # This requires wild_count >= 3, which is impossible (max 2 wilds),
        # but for completeness:
        return None

    def _resolve_straight(
        self, normals: List[Card], wilds: List[Card], wild_count: int,
        length: int, suit: Optional[Suit],
    ) -> Optional[Combo]:
        """Resolve a straight (顺子) or straight flush (同花顺).

        Each rank in the consecutive sequence must appear at most once.
        Wilds fill gaps (missing ranks) and count toward total length.
        """
        if not normals:
            return None

        normal_ranks = [c.rank.value for c in normals]
        min_r = min(normal_ranks)
        max_r = max(normal_ranks)

        # Find the best valid start (prefer highest end rank)
        best: Optional[Combo] = None

        min_start = max(3, max_r - length + 1)
        max_start = min(min_r, 14 - length + 1)

        for start in range(min_start, max_start + 1):
            end = start + length - 1
            # Count normals within range
            rank_set = set(c.rank.value for c in normals)
            in_range_count = sum(1 for r in rank_set if start <= r <= end)
            out_of_range = sum(1 for r in rank_set if r < start or r > end)
            if out_of_range > 0:
                continue
            gaps = length - in_range_count
            if gaps == wild_count:
                combo_type = ComboType.STRAIGHT_FLUSH if suit is not None else ComboType.STRAIGHT
                wild_indices = tuple(
                    i for i, c in enumerate(normals + wilds)
                    if c.is_wild(self.level)
                )
                candidate = Combo(
                    combo_type=combo_type,
                    cards=tuple(normals + wilds),
                    main_rank=Rank(end),
                    length=length,
                    suit=suit,
                    wild_indices=wild_indices,
                    level=self.level,
                )
                if best is None or effective_rank(candidate.main_rank, self.level) > effective_rank(best.main_rank, self.level):
                    best = candidate

        return best

    def _resolve_multi(
        self, normals: List[Card], wilds: List[Card], wild_count: int,
        num_groups: int, per_group: int, combo_type: ComboType,
    ) -> Optional[Combo]:
        """Resolve consecutive pairs or triples.

        num_groups consecutive ranks, each with per_group cards.
        per_group=2 for consecutive pairs, =3 for consecutive triples.
        """
        if not normals:
            return None

        normal_ranks = Counter(c.rank.value for c in normals)
        # Check no rank exceeds per_group
        if any(v > per_group for v in normal_ranks.values()):
            return None

        min_r = min(normal_ranks.keys())
        max_r = max(normal_ranks.keys())

        best: Optional[Combo] = None
        min_start = max(3, max_r - num_groups + 1)
        max_start = min(min_r, 14 - num_groups + 1)

        for start in range(min_start, max_start + 1):
            end = start + num_groups - 1
            needed_wilds = 0
            out_of_range = False
            for rank_val in normal_ranks:
                if start <= rank_val <= end:
                    needed_wilds += max(0, per_group - normal_ranks[rank_val])
                else:
                    out_of_range = True
                    break
            if out_of_range:
                continue
            # Check for "empty" ranks within range (no normal cards) — these also need wilds
            for r in range(start, end + 1):
                if r not in normal_ranks:
                    needed_wilds += per_group

            if needed_wilds == wild_count:
                wild_indices = tuple(
                    i for i, c in enumerate(normals + wilds)
                    if c.is_wild(self.level)
                )
                candidate = Combo(
                    combo_type=combo_type,
                    cards=tuple(normals + wilds),
                    main_rank=Rank(end),
                    length=num_groups * per_group,
                    wild_indices=wild_indices,
                    level=self.level,
                )
                if best is None or effective_rank(candidate.main_rank, self.level) > effective_rank(best.main_rank, self.level):
                    best = candidate

        return best
