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
                                 progress_cb=None, live_cb=None) -> List[Scenario]:
    """Generate endgame scenarios labelled by the exact solver (oracle).

    Each is a random small-hand LEAD position; the solver's optimal lead becomes
    ``expected_play``. Inconclusive solves (timeout / pass / no choice) are skipped.
    ``progress_cb(done, total, message)`` is called after each attempt.
    ``live_cb(detail_dict)`` reports {solved, skipped, of_n} for the dashboard.
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

        if live_cb:
            live_cb({"solved": len(scenarios), "skipped": attempts - len(scenarios),
                     "of_n": n, "attempts": attempts})
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
    """In-memory scenario library with filtering and metadata.

    Synthetic scenarios are persisted to ``data/scenario_bank.json`` on
    every add and reloaded at startup — previously a generated 题库
    evaporated on server restart (only buried inside history rows).
    """

    def __init__(self, scenarios: Optional[List[Scenario]] = None):
        self._scenarios: List[Scenario] = list(scenarios or [])
        self._by_id = {s.id: s for s in self._scenarios}
        self._load_persisted()

    # ---- persistence (synthetic scenarios only) ----

    def _persist_path(self):
        import os
        base = os.environ.get("GUANDAN_SCENARIO_BANK")
        if base:
            return base
        return os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), "data", "scenario_bank.json")

    def _load_persisted(self):
        import json
        path = self._persist_path()
        try:
            import os
            if not os.path.exists(path):
                return
            with open(path, "r", encoding="utf-8") as f:
                items = json.load(f)
            restored = []
            for d in items:
                try:
                    s = Scenario(
                        id=d["id"], name=d["name"], category=d["category"],
                        description=d.get("description", ""),
                        hand=list(d["hand"]),
                        table=d.get("table"), table_player=d.get("table_player", 1),
                        level=d.get("level", 2),
                        opponents={int(k): list(v) for k, v in (d.get("opponents") or {}).items()},
                        played_cards=list(d.get("played_cards") or []),
                        expected_play=d.get("expected_play"),
                        expected_pass=bool(d.get("expected_pass")),
                        reasoning=d.get("reasoning", ""),
                        source=d.get("source", "synthetic"),
                        ground_truth=d.get("ground_truth", "exact"),
                        difficulty=d.get("difficulty", ""),
                        tags=list(d.get("tags") or []),
                    )
                    restored.append(s)
                except Exception:
                    continue
            self.add(restored)
        except Exception:
            pass  # a broken file must never block startup

    def _persist(self):
        import json
        import os
        path = self._persist_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            items = []
            for s in self._scenarios:
                if s.source == "curated":
                    continue  # curated come from code; only synthetic persist
                items.append({
                    "id": s.id, "name": s.name, "category": s.category,
                    "description": s.description, "hand": list(s.hand),
                    "table": s.table, "table_player": s.table_player,
                    "level": s.level,
                    "opponents": {str(k): list(v) for k, v in s.opponents.items()},
                    "played_cards": list(s.played_cards),
                    "expected_play": s.expected_play,
                    "expected_pass": s.expected_pass,
                    "reasoning": s.reasoning,
                    "source": s.source, "ground_truth": s.ground_truth,
                    "difficulty": s.difficulty, "tags": list(s.tags),
                })
            fd, tmp = __import__("tempfile").mkstemp(
                dir=os.path.dirname(path), suffix=".json.tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            pass  # best-effort persistence

    def add(self, scenarios: List[Scenario]):
        added = False
        for s in scenarios:
            if s.id not in self._by_id:
                self._scenarios.append(s)
                self._by_id[s.id] = s
                added = True
        if added:
            self._persist()

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
