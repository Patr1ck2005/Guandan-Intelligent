"""Elo rating for round-robin model comparison."""

from __future__ import annotations

K_FACTOR = 32
INITIAL_RATING = 1500.0


def expected_score(ra: float, rb: float) -> float:
    """Probability player A (rating ra) beats player B (rating rb)."""
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def update(ra: float, rb: float, score_a: float) -> float:
    """New rating for A given actual score_a in {1.0 win, 0.5 draw, 0.0 loss}."""
    ea = expected_score(ra, rb)
    return ra + K_FACTOR * (score_a - ea)
