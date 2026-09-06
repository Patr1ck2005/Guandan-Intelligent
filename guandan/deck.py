"""Deck creation, shuffling, and dealing."""

from __future__ import annotations

import random
from typing import List, Optional, Tuple

from . import constants
from .card import Card


class Deck:

    @staticmethod
    def create() -> List[Card]:
        """Create a sorted 108-card deck (two 54-card packs)."""
        cards = []
        for i in range(constants.TOTAL_CARDS):
            cards.append(Card.from_id(i))
        return cards

    @staticmethod
    def shuffle(cards: List[Card], rng: Optional[random.Random] = None) -> List[Card]:
        """Return a shuffled copy of the cards.

        ``rng``: an explicit Random instance for reproducibility. When None,
        the global RNG is used (legacy behaviour). The self-play harness
        passes an isolated RNG so per-seed games are fully deterministic
        regardless of the caller's global RNG state.
        """
        c = list(cards)
        (rng or random).shuffle(c)
        return c

    @staticmethod
    def deal(cards: List[Card]) -> Tuple[Tuple[Card, ...], ...]:
        """Deal 27 cards to each of 4 players. Returns 4 hands."""
        return (
            tuple(cards[0:27]),
            tuple(cards[27:54]),
            tuple(cards[54:81]),
            tuple(cards[81:108]),
        )
