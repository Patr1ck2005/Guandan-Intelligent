"""Combo comparison: determine if one combo beats another.

Comparison hierarchy (江苏竞赛规则: 王炸＞同花顺＞炸弹＞一般牌型):
1. Rocket (王炸, 4 jokers) beats everything; nothing beats a rocket.
2. Straight flush (同花顺) beats everything except the rocket — including
   ALL normal bombs, regardless of bomb size.
3. Straight flush vs straight flush: higher effective rank wins (length is
   always 5, kept as an invariant guard).
4. Bomb vs bomb (both NORMAL_BOMB): larger bomb (more cards) wins;
   same size → compare rank.
5. Bomb vs non-bomb (neither is SF/rocket): bomb wins.
6. Non-bomb vs non-bomb: must be same type and same length;
   then compare main_rank. The existing combo holds tie advantage (defender
   needs strictly HIGHER).

Note: Combo.is_bomb treats STRAIGHT_FLUSH as part of the "bomb family" for
scoring heuristics (don't waste it cheaply); the legality hierarchy here uses
explicit type checks so the official ordering stays exact.
"""

from __future__ import annotations

from .card import effective_rank
from .combo import Combo, ComboType


def can_beat(new: Combo, existing: Combo) -> bool:
    """Return True if `new` can legally beat `existing`."""
    # Nothing beats a rocket
    if existing.combo_type == ComboType.ROCKET:
        return False

    # Rocket beats everything
    if new.combo_type == ComboType.ROCKET:
        return True

    new_is_sf = new.combo_type == ComboType.STRAIGHT_FLUSH
    old_is_sf = existing.combo_type == ComboType.STRAIGHT_FLUSH
    new_is_bomb = new.combo_type == ComboType.NORMAL_BOMB
    old_is_bomb = existing.combo_type == ComboType.NORMAL_BOMB

    # SF vs SF: higher effective rank (same length — both 5 by parser invariant)
    if new_is_sf and old_is_sf:
        if new.length != existing.length:
            return False
        return effective_rank(new.main_rank, new.level) > \
            effective_rank(existing.main_rank, existing.level)

    # Existing is SF: only rocket (handled above) or a bigger SF beats it
    if old_is_sf:
        return False

    # New is SF: beats everything else — including all normal bombs
    if new_is_sf:
        return True

    # Both are bombs → use bomb comparison
    if new_is_bomb and old_is_bomb:
        return _compare_bombs(new, existing) > 0

    # Bomb vs non-bomb → bomb always wins
    if new_is_bomb:
        return True
    if old_is_bomb:
        return False

    # Both non-bombs → must be exact same type
    if new.combo_type != existing.combo_type:
        return False

    # For sequences (straight, consecutive pairs/triples), lengths must match
    if new.combo_type in (ComboType.STRAIGHT, ComboType.CONSECUTIVE_PAIRS, ComboType.CONSECUTIVE_TRIPLES):
        if new.length != existing.length:
            return False

    new_eff = effective_rank(new.main_rank, new.level)
    exist_eff = effective_rank(existing.main_rank, existing.level)
    return new_eff > exist_eff


def _compare_bombs(a: Combo, b: Combo) -> int:
    """Compare two bombs. Returns >0 if a beats b, <0 if b beats a, 0 if equal."""
    # Different sizes: more cards = bigger bomb
    if a.length != b.length:
        return a.length - b.length
    # Same size: compare effective rank (level-aware)
    a_eff = effective_rank(a.main_rank, a.level)
    b_eff = effective_rank(b.main_rank, b.level)
    return a_eff - b_eff


def sf_beats_non_bomb(combo: Combo) -> bool:
    """True if this combo is a straight flush (same-flush, beats all but rocket)."""
    return combo.combo_type == ComboType.STRAIGHT_FLUSH
