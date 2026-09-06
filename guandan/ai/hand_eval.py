"""Hand evaluation: decompose into combos, compute quality score.

The hand score is a weighted sum of its constituent combos.
Used by the heuristic agent to pick the best play.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..card import Card, Rank
from ..combo import Combo, ComboType
from ..combo_finder import ComboFinder


# Score weights for each combo type per card
_COMBO_WEIGHTS: Dict[ComboType, float] = {
    ComboType.SINGLE:          0.1,
    ComboType.PAIR:            0.4,
    ComboType.TRIPLE:          0.8,
    ComboType.TRIPLE_PAIR:     1.5,
    ComboType.STRAIGHT:        0.6,
    ComboType.CONSECUTIVE_PAIRS: 0.5,
    ComboType.CONSECUTIVE_TRIPLES: 0.7,
    ComboType.NORMAL_BOMB:     1.0,
    ComboType.STRAIGHT_FLUSH:  1.5,
    ComboType.ROCKET:          8.0,
}

# High cards (A, jokers) are more valuable
_RANK_CONTROL_BONUS: Dict[int, float] = {
    14: 0.3,   # Ace
    15: 0.5,   # Small Joker
    16: 0.8,   # Big Joker
}


def hand_score(hand: Tuple[Card, ...], level: int) -> float:
    """Compute a quality score for a hand.

    Higher = better hand. Factors:
      - Bomb count and quality
      - Control cards (jokers, aces)
      - Combo completeness (how well cards group into combos)
      - Wild card flexibility
    """
    finder = ComboFinder(hand, level)
    all_combos = finder.find_all()

    # Count bombs
    bomb_score = 0.0
    bomb_count = 0
    for c in all_combos:
        if c.is_bomb:
            bomb_score += _COMBO_WEIGHTS.get(c.combo_type, 1.0) * c.length
            bomb_count += 1

    # Control cards
    control_score = 0.0
    for card in hand:
        control_score += _RANK_CONTROL_BONUS.get(card.rank.value, 0.0)

    # Combo completeness: find a minimal cover of the hand with combos
    completeness = _estimate_completeness(hand, level)

    # Wild card bonus
    wild_count = sum(1 for c in hand if c.is_wild(level))
    wild_bonus = wild_count * 0.5

    return bomb_score * 1.5 + control_score + completeness * 3.0 + wild_bonus


def _estimate_completeness(hand: Tuple[Card, ...], level: int) -> float:
    """Estimate how completely the hand can be decomposed into combos.

    Returns 0..1 where 1 means every card belongs to a well-structured combo.
    Uses a greedy approach: find the largest valid combo, remove, repeat.
    """
    remaining = list(hand)
    if not remaining:
        return 1.0

    total = len(hand)
    covered = 0

    while remaining:
        finder = ComboFinder(tuple(remaining), level)
        combos = finder.find_all()
        if not combos:
            break
        # Pick the combo that uses the most cards
        best = max(combos, key=lambda c: (c.length, _COMBO_WEIGHTS.get(c.combo_type, 0)))
        covered += best.length
        # Remove used cards
        used_ids = {c.id for c in best.cards}
        remaining = [c for c in remaining if c.id not in used_ids]

    return covered / total if total > 0 else 0.0


def estimate_rounds(hand: Tuple[Card, ...], level: int) -> int:
    """Estimate minimum rounds needed to empty this hand.

    Bombs cost 0 rounds (playable over anything).
    All other combos cost 1 round each.
    Uses a FAST greedy approach without find_all().
    """
    from ..combo_parser import ComboParser
    parser = ComboParser(level)
    remaining = list(hand)
    wilds = [c for c in remaining if c.is_wild(level)]
    normals = [c for c in remaining if not c.is_wild(level)]

    # Group normals by rank
    by_rank: dict = {}
    for c in normals:
        by_rank.setdefault(c.rank, []).append(c)

    used_ids: set = set()
    non_bomb_rounds = 0

    # Pass 1: extract bombs (4+ same rank)
    for rank, cards in sorted(by_rank.items(), key=lambda x: -len(x[1])):
        nc = len(cards)
        total = nc + len(wilds)
        if total >= 4:
            size = min(total, 8)
            wild_need = max(0, size - nc)
            subset = cards[:size - wild_need] + wilds[:wild_need]
            parsed = parser.parse(subset)
            if parsed and parsed.is_bomb:
                for c in subset:
                    used_ids.add(c.id)
                # Remove used wilds from pool
                for w in subset:
                    if w in wilds:
                        wilds.remove(w)

    # Pass 2: rocket (4 jokers)
    big = [c for c in remaining if c.rank == Rank.BIG_JOKER and c.id not in used_ids]
    small = [c for c in remaining if c.rank == Rank.SMALL_JOKER and c.id not in used_ids]
    if len(big) >= 2 and len(small) >= 2:
        parsed = parser.parse(big[:2] + small[:2])
        if parsed and parsed.is_bomb:
            for c in big[:2] + small[:2]:
                used_ids.add(c.id)

    # Remaining cards (non-bomb)
    leftover = [c for c in remaining if c.id not in used_ids]
    if not leftover:
        return non_bomb_rounds

    # Pass 3: greedily find largest combos from remainder
    while len(leftover) > 0:
        best_combo = _find_largest_non_bomb(leftover, parser, level)
        if best_combo is None:
            non_bomb_rounds += len(leftover)  # each remaining card is a single
            break
        for c in best_combo:
            leftover.remove(c)
        non_bomb_rounds += 1

    return non_bomb_rounds


def _find_largest_non_bomb(cards: List[Card], parser, level: int) -> Optional[List[Card]]:
    """Find the largest non-bomb combo from a small set of cards. Fast."""
    n = len(cards)
    if n == 0:
        return None

    # Try straights first (largest combos)
    wilds = [c for c in cards if c.is_wild(level)]
    normals = [c for c in cards if not c.is_wild(level)]
    normal_ranks = sorted(set(c.rank.value for c in normals
                              if c.rank.value <= 14 and c.rank.value != 2))

    for length in (5,):  # 官方规则: straight is exactly 5 cards
        for start in range(3, 14 - length + 2):
            end = start + length - 1
            needed = 0
            subset = []
            for r in range(start, end + 1):
                matches = [c for c in normals if c.rank.value == r]
                if matches:
                    subset.append(matches[0])
                else:
                    needed += 1
            if needed == len(wilds) and len(subset) + needed == length:
                parsed = parser.parse(subset + wilds)
                if parsed and not parsed.is_bomb:
                    return subset + wilds

    # Try consecutive pairs
    if n >= 6:
        for num_pairs in range(min(n // 2, 8), 2, -1):
            for start in range(3, 14 - num_pairs + 2):
                needed = 0
                subset = []
                for r in range(start, start + num_pairs):
                    matches = [c for c in normals if c.rank.value == r]
                    available = min(len(matches), 2)
                    subset.extend(matches[:available])
                    needed += max(0, 2 - available)
                if needed == len(wilds):
                    parsed = parser.parse(subset + wilds)
                    if parsed and not parsed.is_bomb:
                        return subset + wilds

    # Try consecutive triples
    if n >= 6:
        for num_triples in range(min(n // 3, 5), 1, -1):
            for start in range(3, 14 - num_triples + 2):
                needed = 0
                subset = []
                for r in range(start, start + num_triples):
                    matches = [c for c in normals if c.rank.value == r]
                    available = min(len(matches), 3)
                    subset.extend(matches[:available])
                    needed += max(0, 3 - available)
                if needed == len(wilds):
                    parsed = parser.parse(subset + wilds)
                    if parsed and not parsed.is_bomb:
                        return subset + wilds

    # Try triple+pair (三带二)
    by_rank: dict = {}
    for c in normals:
        by_rank.setdefault(c.rank, []).append(c)
    for rank, cs in by_rank.items():
        if len(cs) >= 3:
            for r2, cs2 in by_rank.items():
                if r2 != rank and len(cs2) >= 2:
                    parsed = parser.parse(cs[:3] + cs2[:2])
                    if parsed and not parsed.is_bomb:
                        return cs[:3] + cs2[:2]

    # Pair (biggest rank)
    for rank, cs in sorted(by_rank.items(), key=lambda x: -x[0].value):
        if len(cs) >= 2:
            return cs[:2]

    # Single (biggest rank)
    for rank, cs in sorted(by_rank.items(), key=lambda x: -x[0].value):
        return [cs[0]]

    # Fallback: any single
    return [normals[0]] if normals else [wilds[0]] if wilds else None


def count_control_cards(hand: Tuple[Card, ...]) -> Dict[str, int]:
    """Count control cards in a hand."""
    return {
        "big_joker": sum(1 for c in hand if c.rank == Rank.BIG_JOKER),
        "small_joker": sum(1 for c in hand if c.rank == Rank.SMALL_JOKER),
        "aces": sum(1 for c in hand if c.rank == Rank.A),
        "bombs": 0,  # filled by caller if needed
    }
