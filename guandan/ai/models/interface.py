"""Unified model interface for the test arena.

All models implement this interface to enable:
  - Side-by-side comparison
  - Batch benchmarking
  - Deep analysis
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..player_view import PlayerView
from ...card import Card


@dataclass
class CandidateResult:
    """One candidate play with its score/win_rate and reasoning."""
    combo_type: str
    cards: List[str]           # display strings
    card_ids: List[int]
    score: Optional[float] = None      # heuristic score
    win_rate: Optional[float] = None   # MC win rate
    reasoning: str = ""                # short explanation
    detail: Optional[dict] = None      # structured computation trace


@dataclass
class AnalyzeResult:
    """Complete analysis for one decision point."""
    candidates: List[CandidateResult] = field(default_factory=list)
    choice: Optional[CandidateResult] = None   # what the model chose
    pass_chosen: bool = False
    metrics: Dict[str, Any] = field(default_factory=dict)  # {elapsed_ms, nodes, samples, timed_out, ...}
    model_name: str = ""


class TestableModel:
    """Base class for all testable AI models."""

    name: str = "base"
    description: str = ""
    config_schema: Dict[str, Any] = {}
    default_config: Dict[str, Any] = {}

    def __init__(self, **config):
        self.config = {**self.default_config, **config}

    def analyze(self, view: PlayerView) -> AnalyzeResult:
        raise NotImplementedError

    def choose_play(self, view: PlayerView):
        """Adapter for BaseAgent interface. Returns list of Card or [].

        Calls analyze() and converts the result to match the BaseAgent signature.
        """
        result = self.analyze(view)
        if result.choice and result.choice.combo_type != "PASS":
            card_ids = set(result.choice.card_ids)
            return [c for c in view.my_hand if c.id in card_ids]
        return []
