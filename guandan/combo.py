"""Combo types and Combo dataclass for Guandan."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import FrozenSet, Optional, Tuple

from .card import Card, Rank, Suit


class ComboType(IntEnum):
    """Combo types ordered by priority (higher = more powerful category).

    This priority is used for:
    1. Disambiguation when cards match multiple combo types
    2. Cross-type comparison (bomb vs non-bomb)
    """

    SINGLE = 1
    PAIR = 2
    TRIPLE = 3
    TRIPLE_PAIR = 4          # 三带二
    STRAIGHT = 5             # 顺子 (exactly 5 consecutive singles — fixed length)
    CONSECUTIVE_PAIRS = 6    # 连对/板凳 (3+ consecutive pairs)
    CONSECUTIVE_TRIPLES = 7  # 钢板/飞机 (2+ consecutive triples)
    NORMAL_BOMB = 8          # 普通炸弹 (4-8 of same rank, including wilds)
    STRAIGHT_FLUSH = 9       # 同花顺 (5 consecutive same suit)
    ROCKET = 10              # 火箭/王炸 (Big Joker + Small Joker)


BOMB_TYPES: FrozenSet[ComboType] = frozenset({
    ComboType.NORMAL_BOMB,
    ComboType.STRAIGHT_FLUSH,
    ComboType.ROCKET,
})


@dataclass(frozen=True, slots=True)
class Combo:
    """A fully resolved, immutable card combination.

    Attributes:
        combo_type: The type of this combination.
        cards: The actual card objects forming this combo.
        main_rank: The rank used for comparison (semantics vary by type).
        length: Total number of cards in the combo.
        secondary_rank: For triple+pair: the side component's rank.
        side_type: For triple+pair: 'pair'.
        suit: For straight flush: the suit of the flush.
        wild_indices: Indices of wild cards within the cards tuple.
    """

    combo_type: ComboType
    cards: Tuple[Card, ...]
    main_rank: Rank
    length: int
    level: int = 2
    secondary_rank: Optional[Rank] = None
    side_type: Optional[str] = None
    suit: Optional[Suit] = None
    wild_indices: Tuple[int, ...] = ()

    @property
    def is_bomb(self) -> bool:
        return self.combo_type in BOMB_TYPES

    @property
    def category_priority(self) -> int:
        return self.combo_type.value

    def __repr__(self) -> str:
        parts = [self.combo_type.name, f"main={self.main_rank.name}", f"len={self.length}"]
        if self.suit is not None:
            parts.append(f"suit={self.suit.name}")
        if self.wild_indices:
            parts.append(f"wilds={len(self.wild_indices)}")
        return f"Combo({' '.join(parts)})"


# Shorthand constants for combo type sets used in validation
NON_BOMB_FOLLOW_TYPES: FrozenSet[ComboType] = frozenset({
    ComboType.SINGLE,
    ComboType.PAIR,
    ComboType.TRIPLE,
    ComboType.TRIPLE_PAIR,
    ComboType.STRAIGHT,
    ComboType.CONSECUTIVE_PAIRS,
    ComboType.CONSECUTIVE_TRIPLES,
})
