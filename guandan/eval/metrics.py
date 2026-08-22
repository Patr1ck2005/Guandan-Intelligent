"""Evaluation metrics — accuracy, confidence intervals, regret, stability."""

from __future__ import annotations

import math
from collections import Counter
from typing import List

from .common import _rank_signature


def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple:
    """Wilson score interval for a binomial proportion; returns (low, high)."""
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def is_correct(choice_card_ids, expected_play, expected_pass: bool, pass_chosen: bool) -> bool:
    """Whether a model decision matches the scenario's expected answer (rank-based)."""
    if expected_pass:
        return bool(pass_chosen)
    if expected_play and choice_card_ids:
        return _rank_signature(choice_card_ids) == _rank_signature(expected_play)
    return False


def agreement_rate(labels: List[str]) -> float:
    """Fraction of runs agreeing with the mode label (top-1 consistency)."""
    if not labels:
        return 0.0
    counts = Counter(labels)
    return counts.most_common(1)[0][1] / len(labels)


def entropy(labels: List[str]) -> float:
    """Shannon entropy (nats) of a distribution over discrete labels."""
    if not labels:
        return 0.0
    n = len(labels)
    counts = Counter(labels)
    return -sum((c / n) * math.log(c / n) for c in counts.values())


def regret(chosen_value: float, optimal_value: float) -> float:
    """Value regret: optimal_value - chosen_value (0.0 = optimal)."""
    return max(0.0, optimal_value - chosen_value)
