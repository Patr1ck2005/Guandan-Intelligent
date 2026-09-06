"""Card representation for Guandan.

Cards are identified by integer IDs 0-107. Each of the 2 standard 54-card decks
contributes 54 cards: 52 regular cards (4 suits * 13 ranks) + 2 jokers.

Encoding:
  deck_id = card_id // 54   (0 or 1)
  index   = card_id % 54    (0-53)
    index 0-51:  suit = index // 13, rank_ordinal = index % 13, rank = rank_ordinal + 2
    index 52:    SMALL_JOKER
    index 53:    BIG_JOKER
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Tuple

from . import constants


class Suit(IntEnum):
    CLUBS = 0
    DIAMONDS = 1
    HEARTS = 2
    SPADES = 3
    NONE = 4  # for jokers


class Rank(IntEnum):
    TWO = 2
    THREE = 3
    FOUR = 4
    FIVE = 5
    SIX = 6
    SEVEN = 7
    EIGHT = 8
    NINE = 9
    TEN = 10
    J = 11
    Q = 12
    K = 13
    A = 14
    SMALL_JOKER = 15
    BIG_JOKER = 16


@dataclass(frozen=True, slots=True)
class Card:
    """Immutable card value object. Equality is by identity (id)."""

    id: int
    rank: Rank
    suit: Suit
    deck: int  # 0 or 1; distinguishes the two decks

    @classmethod
    def from_id(cls, card_id: int) -> Card:
        deck = card_id // constants.DECK_SIZE
        idx = card_id % constants.DECK_SIZE
        if idx < 52:
            suit = Suit(idx // 13)
            rank = Rank(idx % 13 + 2)
        elif idx == 52:
            suit, rank = Suit.NONE, Rank.SMALL_JOKER
        else:
            suit, rank = Suit.NONE, Rank.BIG_JOKER
        return cls(id=card_id, rank=rank, suit=suit, deck=deck)

    @property
    def is_joker(self) -> bool:
        return self.rank >= Rank.SMALL_JOKER

    def is_wild(self, level: int) -> bool:
        """Check if this card is the wild card (万能牌/逢人配) at the given level.

        The heart-suit card of the current level rank is the wild card.
        Jokers can never be wild: they are Suit.NONE, so the suit check
        already excludes them (the old is_joker pre-check was redundant).
        Level is always 2..14 (HIGHEST_LEVEL), so ``rank == level`` can
        never match a joker rank (15/16) either.
        """
        return self.suit == Suit.HEARTS and self.rank == level

    @property
    def display(self) -> str:
        suit_str = constants.SUIT_DISPLAY[self.suit]
        rank_str = constants.RANK_DISPLAY[self.rank]
        return f"{suit_str}{rank_str}"

    def __repr__(self) -> str:
        return self.display


def cards_by_rank(cards: Tuple[Card, ...]) -> dict:
    """Group cards by rank. Returns {Rank: [Card, ...]}."""
    groups: dict = {}
    for c in cards:
        groups.setdefault(c.rank, []).append(c)
    return groups


def rank_counts(cards: Tuple[Card, ...]) -> dict:
    """Count cards per rank. Returns {Rank: count}."""
    counts: dict = {}
    for c in cards:
        counts[c.rank] = counts.get(c.rank, 0) + 1
    return counts


def effective_rank(rank: Rank, level: int) -> int:
    """Get the comparison-effective rank value for the given level.

    The level card (级牌) ranks above A(14) and below Small Joker.
    Jokers are shifted up to make room.

    Ordering: 3 < 4 < ... < A(14) < 级牌(15) < SJ(16) < BJ(17)

    Cached: pure function of (rank, level) — 15 ranks x 13 levels = 195
    possible entries; called ~1M times per MC benchmark, so the dict hit
    beats recomputing branches.
    """
    key = (rank, level)
    cached = _EFFECTIVE_RANK_CACHE.get(key)
    if cached is None:
        v = rank.value
        if v >= 15:          # jokers: shift up to make room for level card
            cached = v + 1   # SJ→16, BJ→17
        elif v == level:
            cached = 15      # level card: above A(14), below SJ(16)
        else:
            cached = v
        _EFFECTIVE_RANK_CACHE[key] = cached
    return cached


_EFFECTIVE_RANK_CACHE: dict = {}
