"""Scenario bank — curated puzzles plus oracle-generated synthetic scenarios.

The bank is the "known-answer" backbone of the Eval Lab:
  - curated: hand-crafted puzzles from ``guandan/arena/scenarios.py``
  - synthetic: random small-hand states labelled by the exact endgame solver
Each scenario carries ``source`` / ``ground_truth`` / ``difficulty`` / ``tags``
metadata (fields added to the shared ``Scenario`` dataclass).

Consensus labelling (running several models and keeping only unanimous states)
is a planned follow-up, not part of v1.
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional

from ..card import Card
from ..game_state import GameState
from ..ai.player_view import PlayerView
from ..ai.models.endgame_solver import EndgameExactSolver
from ..arena.scenarios import Scenario, ALL_SCENARIOS


def load_curated_scenarios() -> List[Scenario]:
    """Return the hand-crafted scenario library."""
    return list(ALL_SCENARIOS)


def _random_small_hands(rng: random.Random, max_cards_per_player: int = 4,
                        max_total: int = 16) -> tuple:
    """Deal a random 1..max_cards hand to each player, total <= max_total."""
    sizes = []
    budget = max_total
    for p in range(4):
        min_leave = 3 - p  # leave >=1 card for each remaining player
        upper = min(max_cards_per_player, budget - min_leave)
        upper = max(1, upper)
        size = rng.randint(1, upper)
        sizes.append(size)
        budget -= size

    card_ids = list(range(108))
    rng.shuffle(card_ids)
    hands = []
    idx = 0
    for size in sizes:
        hands.append(tuple(Card.from_id(i) for i in card_ids[idx:idx + size]))
        idx += size
    return tuple(hands)


def generate_synthetic_scenarios(n: int = 50, level: int = 2,
                                 max_cards_per_player: int = 4,
                                 seed: int = 0,
                                 time_limit_ms: int = 5000,
                                 progress_cb=None) -> List[Scenario]:
    """Generate endgame scenarios labelled by the exact solver (oracle).

    Each is a random small-hand LEAD position; the solver's optimal lead becomes
    ``expected_play``. Inconclusive solves (timeout / pass / no choice) are skipped.
    ``progress_cb(done, total, message)`` is called after each attempt.
    """
    rng = random.Random(seed)
    scenarios: List[Scenario] = []
    attempts = 0
    max_attempts = n * 20

    while len(scenarios) < n and attempts < max_attempts:
        attempts += 1
        hands = _random_small_hands(rng, max_cards_per_player)
        state = GameState(level=level, round_number=1, hands=hands,
                          current_player=0, trick_number=1)
        view = PlayerView(state, 0)
        solver = EndgameExactSolver(time_limit_ms=time_limit_ms,
                                    max_cards=max_cards_per_player)

        try:
            result = solver.analyze(view)
            choice = result.choice
            if choice is not None and choice.combo_type != "PASS" and choice.card_ids \
                    and not (choice.detail or {}).get("timed_out"):
                scenarios.append(Scenario(
                    id=f"synthetic-{seed}-{len(scenarios)}",
                    name=f"残局 #{len(scenarios) + 1}",
                    category="endgame",
                    description=(f"自动生成残局（精确解标注）· 等级{level} · 每家≤{max_cards_per_player}张"),
                    hand=[c.id for c in hands[0]],
                    table=None, table_player=0, level=level,
                    opponents={1: [c.id for c in hands[1]], 2: [c.id for c in hands[2]],
                               3: [c.id for c in hands[3]]},
                    expected_play=list(choice.card_ids),
                    expected_pass=False,
                    reasoning="精确解标注的最优出牌",
                    source="synthetic", ground_truth="exact",
                ))
        except Exception:
            pass  # solver error — skip this attempt

        if progress_cb:
            skipped = attempts - len(scenarios)
            progress_cb(len(scenarios), n, f"已生成 {len(scenarios)}/{n}（跳过 {skipped}）")

    return scenarios


def grade_difficulty(correct_fraction_by_id: Dict[str, float],
                     easy_threshold: float = 0.75,
                     hard_threshold: float = 0.35) -> Dict[str, str]:
    """Assign difficulty from a per-scenario fraction of models answering correctly.

    ``correct_fraction_by_id`` maps scenario id → fraction correct across models.
    Returns {id: 'easy' | 'medium' | 'hard'}.
    """
    graded: Dict[str, str] = {}
    for sid, frac in correct_fraction_by_id.items():
        if frac >= easy_threshold:
            graded[sid] = "easy"
        elif frac <= hard_threshold:
            graded[sid] = "hard"
        else:
            graded[sid] = "medium"
    return graded


class ScenarioBank:
    """In-memory scenario library with filtering and metadata."""

    def __init__(self, scenarios: Optional[List[Scenario]] = None):
        self._scenarios: List[Scenario] = list(scenarios or [])
        self._by_id = {s.id: s for s in self._scenarios}

    def add(self, scenarios: List[Scenario]):
        for s in scenarios:
            if s.id not in self._by_id:
                self._scenarios.append(s)
                self._by_id[s.id] = s

    def all(self) -> List[Scenario]:
        return list(self._scenarios)

    def get(self, sid: str) -> Optional[Scenario]:
        return self._by_id.get(sid)

    def filter(self, category: str = None, source: str = None,
               difficulty: str = None, level: int = None) -> List[Scenario]:
        out = self._scenarios
        if category:
            out = [s for s in out if s.category == category]
        if source:
            out = [s for s in out if s.source == source]
        if difficulty:
            out = [s for s in out if s.difficulty == difficulty]
        if level:
            out = [s for s in out if s.level == level]
        return out

    def counts(self) -> dict:
        by_source: Dict[str, int] = {}
        by_difficulty: Dict[str, int] = {}
        for s in self._scenarios:
            by_source[s.source] = by_source.get(s.source, 0) + 1
            d = s.difficulty or "ungraded"
            by_difficulty[d] = by_difficulty.get(d, 0) + 1
        return {"total": len(self._scenarios), "by_source": by_source,
                "by_difficulty": by_difficulty}

    def to_json(self) -> List[dict]:
        return [scenario_json(s) for s in self._scenarios]


def scenario_json(s: Scenario) -> dict:
    """Serialize a Scenario to a JSON-safe dict (same shape as the arena API)."""
    return {
        "id": s.id, "name": s.name, "category": s.category,
        "description": s.description, "level": s.level,
        "hand_size": len(s.hand), "has_table": s.table is not None,
        "source": s.source, "ground_truth": s.ground_truth,
        "difficulty": s.difficulty, "tags": s.tags,
        "expected_pass": s.expected_pass,
        "reasoning": s.reasoning,
    }


# Module-level bank: seeded with curated scenarios; synthetic scenarios are
# appended at runtime via generate_synthetic_scenarios + bank.add(...).
_bank = ScenarioBank(load_curated_scenarios())


def get_bank() -> ScenarioBank:
    return _bank
