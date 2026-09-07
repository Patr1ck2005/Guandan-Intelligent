"""Candidate enumerator — generates plays to evaluate.

Pluggable into MCDecider. Different strategies trade speed vs coverage.
"""

from __future__ import annotations

from typing import List, Tuple

from ..card import Card
from ..combo import Combo
from ..combo_finder import ComboFinder
from ..combo_compare import can_beat
from .player_view import PlayerView


class CandidateEnumerator:
    """Base class for candidate generation."""

    name: str = "base"
    description: str = ""

    def enumerate(self, hand: Tuple[Card, ...], view: PlayerView,
                  is_lead: bool, table_combo: Combo | None,
                  level: int) -> List[Combo]:
        raise NotImplementedError


class FullEnumerator(CandidateEnumerator):
    """Enumerate all valid same-type responses + bomb option. Most complete, slower."""

    name = "全面枚举"
    description = "穷举所有同类型牌型+炸弹"

    def enumerate(self, hand, view, is_lead, table_combo, level):
        finder = ComboFinder(hand, level)
        if is_lead:
            from .agent import _generate_lead_candidates
            return _generate_lead_candidates(finder, hand)
        else:
            from .agent import _enumerate_responses
            return _enumerate_responses(hand, table_combo, finder, level)


class TopNEnumerator(CandidateEnumerator):
    """Pre-score with any available scorer, take top N. Faster."""

    name = "预筛TopN"
    description = "预评分后取前N个候选"

    def __init__(self, top_n: int = 5, pre_scorer: str = "blind"):
        self.top_n = top_n
        self.pre_scorer = pre_scorer

    def enumerate(self, hand, view, is_lead, table_combo, level):
        finder = ComboFinder(hand, level)
        if is_lead:
            from .agent import _generate_lead_candidates
            candidates = _generate_lead_candidates(finder, hand)
        else:
            from .agent import _enumerate_responses
            candidates = _enumerate_responses(hand, table_combo, finder, level)

        if len(candidates) <= self.top_n:
            return candidates

        # Pre-score with chosen scorer
        scored = []
        for c in candidates:
            used_ids = {x.id for x in c.cards}
            hand_after = tuple(x for x in hand if x.id not in used_ids)
            s = self._score(c, hand, hand_after, table_combo, level, view)
            scored.append((c, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [c for c, _ in scored[:self.top_n]]

    def _score(self, c, hand, hand_after, table_combo, level, view):
        if self.pre_scorer == "blind":
            from .scorer import score_play
            from .params import DEFAULT_PARAMS
            return score_play(c, hand, hand_after, table_combo, level, DEFAULT_PARAMS)
        elif self.pre_scorer in ("informed", "round"):
            # informed/round read view._state (all four real hands). Letting
            # them pre-filter candidates handed the outer MC decider perfect
            # information before any sampling — a fairness hole the default
            # config never hit but a config panel option did. Downgrade to
            # the blind scorer instead of honoring it.
            from .scorer import score_play
            from .params import DEFAULT_PARAMS
            return score_play(c, hand, hand_after, table_combo, level, DEFAULT_PARAMS)
        return 0


# Registry (memory uses lazy import to avoid circular dependency)
def _get_memory_enumerator():
    from .memory_enumerator import MemoryAwareEnumerator
    return MemoryAwareEnumerator


_ENUMERATORS: dict = {
    "full": FullEnumerator,
    "top_n": TopNEnumerator,
    "memory": _get_memory_enumerator,
}


def list_enumerators() -> List[str]:
    return list(_ENUMERATORS.keys())


def create_enumerator(enum_id: str, **kw) -> CandidateEnumerator:
    # Unknown ids used to silently fall back to FullEnumerator, which quietly
    # changed what a config (e.g. regression's typo'd "fast") actually ran.
    factory_or_cls = _ENUMERATORS.get(enum_id)
    if factory_or_cls is None:
        raise ValueError(f"unknown enumerator id: {enum_id!r} "
                         f"(valid: {sorted(_ENUMERATORS)})")
    if callable(factory_or_cls) and not isinstance(factory_or_cls, type):
        return factory_or_cls()  # lazy factory
    return factory_or_cls(**kw)
