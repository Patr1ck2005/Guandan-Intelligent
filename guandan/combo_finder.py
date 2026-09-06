"""Enumerate valid combos from a hand. Used by AI for decision-making.

For performance, this module provides both full enumeration (for analysis)
and fast targeted methods (for agent play).
"""

from __future__ import annotations

import random
from itertools import combinations
from typing import Dict, List, Optional, Tuple

from .card import Card, Rank, Suit, effective_rank
from .combo import Combo, ComboType
from .combo_parser import ComboParser
from .combo_compare import can_beat
from .constants import STRAIGHT_LEN


class ComboFinder:
    """Find playable combos from a hand."""

    def __init__(self, hand: Tuple[Card, ...], level: int):
        self.hand = tuple(hand)
        self.level = level
        self._parser = ComboParser(level)
        self._by_rank: Dict[Rank, List[Card]] = {}
        for c in hand:
            self._by_rank.setdefault(c.rank, []).append(c)

        self.wilds = [c for c in hand if c.is_wild(level)]
        self.normals = [c for c in hand if not c.is_wild(level)]
        self.wild_count = len(self.wilds)

    # ------------------------------------------------------------------
    # Fast play methods for agents
    # ------------------------------------------------------------------

    def pick_lead(self) -> Optional[Combo]:
        """Pick a play when leading. Prefers singles, then pairs.
        Does not enumerate all combos — uses heuristics for speed."""
        cards = list(self.hand)
        if not cards:
            return None

        # Prefer a single (40% chance) or pair (30%) or triple (20%) or other (10%)
        roll = random.random()

        # Single
        if roll < 0.4:
            c = random.choice(cards)
            return self._make_single(c)

        # Pair
        if roll < 0.7:
            for rank, cs in self._by_rank.items():
                if len(cs) >= 2:
                    pair = random.sample(cs, 2)
                    combo = self._parser.parse(pair)
                    if combo:
                        return combo
            # Try wild-augmented pair
            if self.wilds:
                c = random.choice(cards)
                if c not in self.wilds:
                    combo = self._parser.parse([c, self.wilds[0]])
                    if combo:
                        return combo
            return self._make_single(random.choice(cards))

        # Triple
        if roll < 0.9:
            for rank, cs in self._by_rank.items():
                if len(cs) >= 3:
                    triple = random.sample(cs, 3)
                    combo = self._parser.parse(triple)
                    if combo:
                        return combo
            # Try with wilds
            for rank, cs in self._by_rank.items():
                if len(cs) >= 2 and self.wild_count >= 1:
                    combo = self._parser.parse(cs[:2] + [self.wilds[0]])
                    if combo:
                        return combo
            return self._make_single(random.choice(cards))

        # Small straight or bomb
        return self._make_single(random.choice(cards))

    def pick_response(self, table_combo: Combo) -> Optional[Combo]:
        """Pick a response that beats the table combo.
        Uses targeted search instead of full enumeration."""
        ct = table_combo.combo_type

        if ct == ComboType.SINGLE:
            return self._beat_single(table_combo)
        elif ct == ComboType.PAIR:
            return self._beat_pair(table_combo)
        elif ct == ComboType.TRIPLE:
            return self._beat_triple(table_combo)
        elif ct == ComboType.TRIPLE_PAIR:
            return self._beat_triple_side(table_combo, 2)
        elif ct == ComboType.STRAIGHT:
            return self._beat_straight(table_combo)
        elif ct == ComboType.STRAIGHT_FLUSH:
            return self._beat_straight_flush(table_combo)
        elif ct == ComboType.CONSECUTIVE_PAIRS:
            return self._beat_consecutive_pairs(table_combo)
        elif ct == ComboType.CONSECUTIVE_TRIPLES:
            return self._beat_consecutive_triples(table_combo)
        elif ct == ComboType.NORMAL_BOMB:
            return self._beat_normal_bomb(table_combo)
        elif ct == ComboType.ROCKET:
            return None  # nothing beats rocket

        return None

    # ------------------------------------------------------------------
    # Full enumeration (for analysis, use sparingly)
    # ------------------------------------------------------------------

    def find_all(self) -> List[Combo]:
        """Return every valid combo from this hand.
        WARNING: O(2^n) — use only for analysis with small hands."""
        results: List[Combo] = []
        card_list = list(self.hand)

        # Singles
        for c in card_list:
            results.append(self._make_single(c))

        # Pairs
        for rank, cards in self._by_rank.items():
            if len(cards) >= 2:
                for pair in combinations(cards, 2):
                    combo = self._parser.parse(list(pair))
                    if combo:
                        results.append(combo)
        # Wild + normal pairs
        for wild in self.wilds:
            for c in self.normals:
                combo = self._parser.parse([wild, c])
                if combo and combo.combo_type == ComboType.PAIR:
                    results.append(combo)
        if len(self.wilds) >= 2:
            combo = self._parser.parse(list(self.wilds[:2]))
            if combo:
                results.append(combo)

        # Triples
        for rank, cards in self._by_rank.items():
            if len(cards) >= 3:
                for triple in combinations(cards, 3):
                    combo = self._parser.parse(list(triple))
                    if combo:
                        results.append(combo)
        # Wild-augmented triples
        for rank, cards in self._by_rank.items():
            if len(cards) >= 2 and self.wild_count >= 1:
                for pair in combinations(cards, 2):
                    combo = self._parser.parse(list(pair) + [self.wilds[0]])
                    if combo and combo.combo_type == ComboType.TRIPLE:
                        results.append(combo)

        # Triple+Pair (三带二)
        for rank, cards in self._by_rank.items():
            if len(cards) >= 3:
                triples = list(combinations(cards, 3))
                for triple in triples:
                    for r2, sc in self._by_rank.items():
                        if r2 != rank and len(sc) >= 2:
                            for side_pair in combinations(sc, 2):
                                combo = self._parser.parse(list(triple) + list(side_pair))
                                if combo and combo.combo_type == ComboType.TRIPLE_PAIR:
                                    results.append(combo)

        # Bombs (4-8)
        for rank, cards in self._by_rank.items():
            nc = len(cards)
            for size in range(4, min(nc + self.wild_count, 8) + 1):
                normal_need = min(nc, size)
                for normal_subset in combinations(cards, normal_need):
                    wild_need = size - normal_need
                    all_cards = list(normal_subset) + self.wilds[:wild_need]
                    combo = self._parser.parse(all_cards)
                    if combo and combo.is_bomb:
                        results.append(combo)

        # Straights
        self._find_straights(results)

        # Consecutive pairs
        self._find_consecutive_pairs(results)

        # Consecutive triples
        self._find_consecutive_triples(results)

        # Rocket (4 jokers: 2 Big + 2 Small)
        big = [c for c in card_list if c.rank == Rank.BIG_JOKER]
        small = [c for c in card_list if c.rank == Rank.SMALL_JOKER]
        if len(big) >= 2 and len(small) >= 2:
            combo = self._parser.parse(big[:2] + small[:2])
            if combo:
                results.append(combo)

        # Deduplicate
        seen: dict = {}
        for c in results:
            key = (c.combo_type, c.main_rank, c.length,
                   c.secondary_rank, tuple(sorted(x.id for x in c.cards)))
            seen[key] = c
        return list(seen.values())

    def find_legal_responses(self, table_combo: Combo) -> List[Combo]:
        """Find all combos that beat table_combo. Use sparingly."""
        return [c for c in self.find_all() if can_beat(c, table_combo)]

    # ------------------------------------------------------------------
    # Targeted response methods (fast, no full enumeration)
    # ------------------------------------------------------------------

    def _beat_single(self, target: Combo) -> Optional[Combo]:
        t_eff = effective_rank(target.main_rank, self.level)
        # Find the lowest normal card that beats the target
        best = None
        for c in self.hand:
            if effective_rank(c.rank, self.level) > t_eff:
                if best is None or effective_rank(c.rank, self.level) < effective_rank(best.main_rank, self.level):
                    best = self._make_single(c)
        # Bombs always work
        if best is None:
            best = self._find_any_bomb()
        return best

    def _beat_pair(self, target: Combo) -> Optional[Combo]:
        t_eff = effective_rank(target.main_rank, self.level)
        best = None
        for rank, cards in self._by_rank.items():
            if effective_rank(rank, self.level) > t_eff and len(cards) >= 2:
                combo = self._parser.parse(list(cards[:2]))
                if combo and (best is None or effective_rank(rank, self.level) < effective_rank(best.main_rank, self.level)):
                    best = combo
        # Try wild
        if best is None:
            for c in self.normals:
                if effective_rank(c.rank, self.level) > t_eff and self.wild_count >= 1:
                    combo = self._parser.parse([c, self.wilds[0]])
                    if combo and (best is None or effective_rank(c.rank, self.level) < effective_rank(best.main_rank, self.level)):
                        best = combo
        if best is None:
            best = self._find_any_bomb()
        return best

    def _beat_triple(self, target: Combo) -> Optional[Combo]:
        t_eff = effective_rank(target.main_rank, self.level)
        best = None
        for rank, cards in self._by_rank.items():
            if effective_rank(rank, self.level) > t_eff and len(cards) >= 3:
                combo = self._parser.parse(list(cards[:3]))
                if combo and (best is None or effective_rank(rank, self.level) < effective_rank(best.main_rank, self.level)):
                    best = combo
        # Try with wilds
        for rank, cards in self._by_rank.items():
            if effective_rank(rank, self.level) > t_eff and len(cards) >= 2 and self.wild_count >= 1:
                combo = self._parser.parse(cards[:2] + [self.wilds[0]])
                if combo and (best is None or effective_rank(rank, self.level) < effective_rank(best.main_rank, self.level)):
                    best = combo
        if best is None:
            best = self._find_any_bomb()
        return best

    def _beat_triple_side(self, target: Combo, side_size: int) -> Optional[Combo]:
        """Find a triple+pair (三带二) that beats the target."""
        t_eff = effective_rank(target.main_rank, self.level)
        for rank, cards in self._by_rank.items():
            if effective_rank(rank, self.level) <= t_eff:
                continue
            nc = len(cards)
            # Try with 0 wilds for triple
            if nc >= 3:
                triples = list(combinations(cards, 3))
                for triple in triples:
                    for r2, sc in self._by_rank.items():
                        if r2 != rank and len(sc) >= 2:
                            combo = self._parser.parse(list(triple) + list(sc[:2]))
                            if combo:
                                return combo
            # With 1 wild for triple
            if nc >= 2 and self.wild_count >= 1:
                triple_base = list(cards[:2]) + [self.wilds[0]]
                for r2, sc in self._by_rank.items():
                    if r2 != rank and len(sc) >= 2:
                        combo = self._parser.parse(triple_base + list(sc[:2]))
                        if combo:
                            return combo
        return self._find_any_bomb()

    def _beat_straight(self, target: Combo) -> Optional[Combo]:
        # Level card or joker: no straight can beat it (effective rank >= 15)
        if effective_rank(target.main_rank, self.level) >= 15:
            return None
        length = target.length
        t_end = target.main_rank.value
        # Note: the parser cascade resolves a same-suit 5-run as STRAIGHT_FLUSH
        # before STRAIGHT — legal now that can_beat lets SF beat plain straights.
        return self._find_straight_beating(length, t_end)

    def _beat_straight_flush(self, target: Combo) -> Optional[Combo]:
        """Respond to a straight flush: a bigger SF of ANY suit.

        Per 江苏竞赛规则: SF beats ALL normal bombs, so bombs are NOT valid
        responses to an SF — only a higher SF can follow (a rocket in hand is
        appended by callers via the can_beat-validated bomb path). Wild cards
        are always hearts, so wild-containing SFs are hearts-suited.
        """
        length = target.length  # 5 by parser invariant
        t_end = target.main_rank.value
        t_eff = effective_rank(target.main_rank, self.level)
        best: Optional[Combo] = None
        best_eff = -1
        for suit in (Suit.CLUBS, Suit.DIAMONDS, Suit.HEARTS, Suit.SPADES):
            for end in range(t_end + 1, 15):
                start = end - length + 1
                if start < 3:
                    continue
                subset = []
                missing = 0
                ok = True
                for r in range(start, end + 1):
                    r_rank = Rank(r)
                    matches = [c for c in self.normals
                               if c.rank == r_rank and c.suit == suit]
                    if matches:
                        subset.append(matches[0])
                    elif suit == Suit.HEARTS and missing < self.wild_count:
                        missing += 1  # wild (always hearts) fills the gap
                    else:
                        ok = False
                        break
                if not ok:
                    continue
                combo = self._parser.parse(subset + self.wilds[:missing])
                if combo and combo.combo_type == ComboType.STRAIGHT_FLUSH:
                    eff = effective_rank(combo.main_rank, self.level)
                    if t_eff < eff <= 15 and eff > best_eff:
                        best, best_eff = combo, eff
        return best

    def _beat_consecutive_pairs(self, target: Combo) -> Optional[Combo]:
        if effective_rank(target.main_rank, self.level) >= 15:
            return None
        num_pairs = target.length // 2
        t_end = target.main_rank.value
        return self._find_consecutive_pairs_beating(num_pairs, t_end)

    def _beat_consecutive_triples(self, target: Combo) -> Optional[Combo]:
        if effective_rank(target.main_rank, self.level) >= 15:
            return None
        num_triples = target.length // 3
        t_end = target.main_rank.value
        return self._find_consecutive_triples_beating(num_triples, t_end)

    def _beat_normal_bomb(self, target: Combo) -> Optional[Combo]:
        # Bigger bomb or same-size higher effective rank
        best = None
        for rank, cards in self._by_rank.items():
            nc = len(cards)
            total = nc + self.wild_count
            if total < 4:
                continue
            for size in range(4, min(total, 8) + 1):
                if size > target.length:
                    wild_need = max(0, size - nc)
                    combo = self._parser.parse(cards[:size - wild_need] + self.wilds[:wild_need])
                    if combo and combo.is_bomb:
                        return combo
                elif size == target.length:
                    if effective_rank(rank, self.level) > effective_rank(target.main_rank, self.level):
                        wild_need = max(0, size - nc)
                        combo = self._parser.parse(cards[:size - wild_need] + self.wilds[:wild_need])
                        if combo and combo.is_bomb:
                            return combo
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_single(self, card: Card) -> Combo:
        return Combo(
            combo_type=ComboType.SINGLE,
            cards=(card,),
            main_rank=card.rank,
            length=1,
            wild_indices=(0,) if card.is_wild(self.level) else (),
            level=self.level,
        )

    def _find_any_bomb(self) -> Optional[Combo]:
        """Find any bomb in the hand."""
        # Rocket (4 jokers: 2 Big + 2 Small)
        big = [c for c in self.hand if c.rank == Rank.BIG_JOKER]
        small = [c for c in self.hand if c.rank == Rank.SMALL_JOKER]
        if len(big) >= 2 and len(small) >= 2:
            combo = self._parser.parse(big[:2] + small[:2])
            if combo:
                return combo
        # Normal bomb
        for rank, cards in self._by_rank.items():
            nc = len(cards)
            total = nc + self.wild_count
            if total >= 4:
                size = min(total, 8)
                wild_need = max(0, size - nc)
                all_cards = cards[:size - wild_need] + self.wilds[:wild_need]
                combo = self._parser.parse(all_cards)
                if combo and combo.is_bomb:
                    return combo
        return None

    def _find_straights(self, results: List[Combo]):
        """Find all straights/straight flushes (for full enumeration).

        官方规则: straights are exactly 5 cards — a single length.
        """
        normal_ranks_set = sorted(set(
            c.rank for c in self.normals
            if c.rank not in (Rank.TWO, Rank.SMALL_JOKER, Rank.BIG_JOKER)
        ))
        for length in (STRAIGHT_LEN,):
            for start in range(3, 14 - length + 2):
                end = start + length - 1
                needed = 0
                subset = []
                for r in range(start, end + 1):
                    r_rank = Rank(r)
                    matches = [c for c in self.normals if c.rank == r_rank]
                    if matches:
                        subset.append(matches[0])
                    else:
                        needed += 1
                if needed == self.wild_count and len(subset) + needed == length:
                    combo = self._parser.parse(subset + self.wilds)
                    if combo:
                        results.append(combo)

    def _find_straight_beating(self, length: int, min_end: int) -> Optional[Combo]:
        """Find a straight of given length with end rank > min_end."""
        for end in range(min_end + 1, 15):  # up to A
            start = end - length + 1
            if start < 3:
                continue
            needed = 0
            subset = []
            for r in range(start, end + 1):
                r_rank = Rank(r)
                matches = [c for c in self.normals if c.rank == r_rank]
                if matches:
                    subset.append(matches[0])
                else:
                    needed += 1
            if needed == self.wild_count and len(subset) + needed == length:
                combo = self._parser.parse(subset + self.wilds)
                if combo:
                    return combo
        return self._find_any_bomb()

    def _find_consecutive_pairs(self, results: List[Combo]):
        for num_pairs in range(3, 8):
            for start in range(3, 14 - num_pairs + 2):
                needed = 0
                subset = []
                for r in range(start, start + num_pairs):
                    cs = self._by_rank.get(Rank(r), [])
                    available = min(len(cs), 2)
                    subset.extend(cs[:available])
                    needed += max(0, 2 - available)
                if needed == self.wild_count:
                    combo = self._parser.parse(subset + self.wilds)
                    if combo and combo.combo_type == ComboType.CONSECUTIVE_PAIRS:
                        results.append(combo)

    def _find_consecutive_pairs_beating(self, num_pairs: int, min_end: int) -> Optional[Combo]:
        for end in range(min_end + 1, 15):
            start = end - num_pairs + 1
            if start < 3:
                continue
            needed = 0
            subset = []
            for r in range(start, end + 1):
                cs = self._by_rank.get(Rank(r), [])
                available = min(len(cs), 2)
                subset.extend(cs[:available])
                needed += max(0, 2 - available)
            if needed == self.wild_count:
                combo = self._parser.parse(subset + self.wilds)
                if combo and combo.combo_type == ComboType.CONSECUTIVE_PAIRS:
                    return combo
        return self._find_any_bomb()

    def _find_consecutive_triples(self, results: List[Combo]):
        for num_triples in range(2, 5):
            for start in range(3, 14 - num_triples + 2):
                needed = 0
                subset = []
                for r in range(start, start + num_triples):
                    cs = self._by_rank.get(Rank(r), [])
                    available = min(len(cs), 3)
                    subset.extend(cs[:available])
                    needed += max(0, 3 - available)
                if needed == self.wild_count:
                    combo = self._parser.parse(subset + self.wilds)
                    if combo and combo.combo_type == ComboType.CONSECUTIVE_TRIPLES:
                        results.append(combo)

    def _find_consecutive_triples_beating(self, num_triples: int, min_end: int) -> Optional[Combo]:
        for end in range(min_end + 1, 15):
            start = end - num_triples + 1
            if start < 3:
                continue
            needed = 0
            subset = []
            for r in range(start, end + 1):
                cs = self._by_rank.get(Rank(r), [])
                available = min(len(cs), 3)
                subset.extend(cs[:available])
                needed += max(0, 3 - available)
            if needed == self.wild_count:
                combo = self._parser.parse(subset + self.wilds)
                if combo and combo.combo_type == ComboType.CONSECUTIVE_TRIPLES:
                    return combo
        return self._find_any_bomb()
