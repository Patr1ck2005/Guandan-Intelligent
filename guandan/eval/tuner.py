"""AIParams auto-tuner (stage 4.3) — random search with a hard budget.

Search space: the tunable numeric AIParams fields with sane bounds
(teammate/opp-model weights + the classic scoring weights). The tuner
plays candidate-weight sets against the fixed reference lineup
(blind-default) using the multiprocess self-play harness, seeds fixed
and recorded, and reports the best set with a Wilson CI on its win
rate.

Honest-budget rules:
  - ``max_games`` caps total games per candidate evaluation; the caller
    passes a wall-clock budget too. Both are hard limits.
  - Every candidate's params + seed + result go to the caller (the eval
    job persists them in the history store).
  - No claim of optimality: this is random search, and the report says so.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from ..ai.params import AIParams
from .metrics import wilson_interval

# name -> (low, high); only float fields worth tuning end-to-end
SEARCH_SPACE: dict = {
    "efficiency_weight": (0.0, 40.0),
    "round_weight": (0.0, 20.0),
    "card_usage_weight": (0.0, 2.0),
    "support_teammate_lead": (0.0, 12.0),
    "block_opponent_lead": (0.0, 10.0),
    "push_teammate_endgame": (0.0, 10.0),
    "opp_model_cover_bonus": (0.0, 8.0),
}

# Endpoint knobs are excluded on purpose (pass_threshold / bomb penalties /
# sim_pass_prob change pass dynamics non-monotonically and made the early
# search unstable; the classic weights above dominate the score landscape).


@dataclass
class TunerBudget:
    max_candidates: int = 12
    games_per_candidate: int = 8
    max_seconds: float = 600.0   # hard wall clock
    max_workers: int = 2
    base_seed: int = 7000

    def clamp(self):
        self.max_candidates = max(1, min(int(self.max_candidates), 60))
        self.games_per_candidate = max(1, min(int(self.games_per_candidate), 60))
        self.max_seconds = max(30.0, min(float(self.max_seconds), 3600.0))
        self.max_workers = max(1, min(int(self.max_workers), 8))


def _random_candidate(rng: random.Random) -> dict:
    return {name: round(rng.uniform(lo, hi), 2)
            for name, (lo, hi) in SEARCH_SPACE.items()}


def _mutate(base: dict, rng: random.Random, sigma: float = 0.25) -> dict:
    """Local refinement: gaussian jitter around a good candidate."""
    out = {}
    for name, (lo, hi) in SEARCH_SPACE.items():
        v = base.get(name, 0.0) + rng.gauss(0.0, sigma * (hi - lo))
        out[name] = round(min(hi, max(lo, v)), 2)
    return out


def run_tuner(budget: TunerBudget, progress_cb=None, live_cb=None,
              cancel_check: Optional[Callable[[], bool]] = None) -> dict:
    """Random search (coarse) + local refinement around the best.

    Returns a JSON-safe dict: best weights, its win rate + Wilson CI,
    all candidate trials, budget actually consumed, and the seed.

    Cancellation: ``cancel_check`` returning True raises ``JobCancelled``
    (imported lazily to avoid the API-layer dependency) so a stopped tuner
    behaves like every other job — no result, nothing persisted. The old
    bool-driven early-stop returned a normal "done" result that got stored
    with ``stopped_early`` and offered its half-searched weights for apply.
    """
    from .eval_api import JobCancelled

    def _check_cancel():
        if cancel_check and cancel_check():
            raise JobCancelled("cancelled")

    budget.clamp()
    t0 = time.time()
    rng = random.Random(budget.base_seed)
    reference = {"id": "blind", "label": "blind-base"}  # fixed reference lineup
    trials: List[dict] = []
    best: Optional[dict] = None

    from .selfplay import run_match

    total_units = budget.max_candidates * 2  # coarse + refinement phase
    done_units = 0

    def _p(msg):
        if progress_cb:
            progress_cb(done_units, total_units, msg)

    def _evaluate(weights: dict, seed: int) -> dict:
        profile = {"id": "blind", "label": "cand", **weights}
        m = run_match(profile, reference,
                      n_games=budget.games_per_candidate,
                      base_seed=seed, agent_timeout_ms=75000,
                      max_workers=budget.max_workers)
        d = m.to_dict()
        n = d["a_wins"] + d["b_wins"]
        wr = d["a_wins"] / n if n else 0.0
        lo, hi = wilson_interval(d["a_wins"], n) if n else (0.0, 0.0)
        return {
            "weights": weights, "seed": seed,
            "wins": d["a_wins"], "losses": d["b_wins"], "games": n,
            "errors": d["errors"],
            "win_rate": round(wr, 3),
            "wilson": [round(lo * 100, 1), round(hi * 100, 1)],
            "elapsed_s": d["elapsed_s"],
        }

    # Phase 1: coarse random search
    for i in range(budget.max_candidates):
        _check_cancel()
        if time.time() - t0 > budget.max_seconds:
            break
        cand = _random_candidate(rng)
        seed = budget.base_seed + i * 1000
        _p(f"粗搜 {i + 1}/{budget.max_candidates} · 胜率检验 {budget.games_per_candidate} 局")
        trial = _evaluate(cand, seed)
        trials.append(trial)
        done_units += 1
        if best is None or trial["win_rate"] > best["win_rate"]:
            best = trial
        if live_cb:
            live_cb({"phase": "coarse", "done": i + 1,
                     "of": budget.max_candidates,
                     "best_wr": best["win_rate"],
                     "best_wilson": best["wilson"],
                     "best_weights": best["weights"],
                     "trials": trials[-6:]})

    # Phase 2: local refinement around the best (if time + units remain)
    n_refine = max(1, budget.max_candidates // 3)
    for i in range(n_refine):
        _check_cancel()
        if time.time() - t0 > budget.max_seconds:
            break
        if best is None:
            break
        cand = _mutate(best["weights"], rng)
        seed = budget.base_seed + 500000 + i * 1000
        _p(f"细化 {i + 1}/{n_refine} · 围绕最优微调")
        trial = _evaluate(cand, seed)
        trials.append(trial)
        done_units += 1
        if trial["win_rate"] > best["win_rate"]:
            best = trial
        if live_cb:
            live_cb({"phase": "refine", "done": i + 1, "of": n_refine,
                     "best_wr": best["win_rate"],
                     "best_wilson": best["wilson"],
                     "best_weights": best["weights"],
                     "trials": trials[-6:]})

    verdict = "inconclusive"
    if best and best["win_rate"] > 0.5:
        lo = best["wilson"][0]
        verdict = "effective" if lo > 50.0 else "inconclusive"
    return {
        "best": best,
        "verdict": verdict,
        "trials": trials,
        "budget": {
            "max_candidates": budget.max_candidates,
            "games_per_candidate": budget.games_per_candidate,
            "max_workers": budget.max_workers,
            "base_seed": budget.base_seed,
            "elapsed_s": round(time.time() - t0, 1),
            # Cancelled runs never reach here (JobCancelled propagates);
            # a time-budget exhaustion is a normal completed search.
            "stopped_early": False,
        },
        "search_space": {k: list(v) for k, v in SEARCH_SPACE.items()},
        "note": "random search + local refinement; not guaranteed optimal",
    }
