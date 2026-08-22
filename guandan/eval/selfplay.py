"""Self-play harness — headless match and round-robin evaluation.

Reuses ``Game.play_game()`` to run full 2v2 games and reports win rates / Elo.
Every result records the exact agent profiles + seed used, so results are
honest and reproducible (sequential mode) — no hidden parameter reductions.
Individual game errors (e.g. an agent bug) are recorded rather than aborting
the whole match.
"""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import List

from ..game import Game
from ..ai.registry import create_agent_for_player
from .elo import update as elo_update


def build_player_agent(profile: dict):
    """Build a game-playing agent (``choose_play``) from a decider profile.

    Unlike ``eval.common._make_arena_agent`` (which maps "blind" to the
    BlindScorer test model), this routes through ``registry.create_agent_for_player``
    so every decider is the actual game agent (BlindAgent / MCDecider /
    ISMCTSDecider / LLMAgent).
    """
    model_id = profile.get("id", "blind")
    decider_params = {k: v for k, v in profile.items()
                      if k not in ("id", "label")}

    inner_id = decider_params.get("inner", "informed")
    enum_id = decider_params.get("enumerator", "top_n")
    inner_params = {k[6:]: v for k, v in decider_params.items()
                    if k.startswith("inner.")}
    enum_params = {k[11:]: v for k, v in decider_params.items()
                   if k.startswith("enumerator.")}
    decider_params = {k: v for k, v in decider_params.items()
                      if not k.startswith("inner.") and not k.startswith("enumerator.")}

    config = {
        "players": {0: {"decider": model_id, "params": decider_params}},
        "decider": {"default_model": model_id, "model_params": {model_id: decider_params}},
        "inner_model": {"model_params": {
            "blind": {}, "informed": {}, "round": {}, "exact": {},
            inner_id: inner_params,
        }},
        "enumerator": {"model_params": {
            "full": {}, "top_n": {}, "memory": {},
            enum_id: enum_params,
        }},
    }
    return create_agent_for_player(config, 0)


@dataclass
class GameRecord:
    seed: int
    winning_team: int   # 0 = model A (seats 0/2), 1 = model B (seats 1/3), -1 = error
    rounds_played: int
    error: str = ""


@dataclass
class MatchResult:
    a: dict
    b: dict
    n_games: int
    a_wins: int
    b_wins: int
    errors: int = 0
    games: List[GameRecord] = field(default_factory=list)
    elapsed_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "a": self.a,
            "b": self.b,
            "n_games": self.n_games,
            "a_wins": self.a_wins,
            "b_wins": self.b_wins,
            "errors": self.errors,
            "games": [{"seed": g.seed, "winning_team": g.winning_team,
                       "rounds_played": g.rounds_played, "error": g.error}
                      for g in self.games],
            "elapsed_s": round(self.elapsed_s, 2),
        }


def run_match(profile_a: dict, profile_b: dict, n_games: int = 10,
              base_seed: int = 0, agent_timeout_ms: float = 75000,
              max_workers: int = 1, progress_cb=None) -> MatchResult:
    """Run a 2v2 match (A seats 0/2, B seats 1/3) over ``n_games`` games.

    ``max_workers=1`` (default) is sequential and deterministic for a given
    seed. ``max_workers>1`` parallelizes across games but shares the global
    RNG, so individual games are still valid but not seed-reproducible.
    """
    t0 = time.time()

    def play_one(i: int) -> GameRecord:
        seed = base_seed + i
        random.seed(seed)
        agents = [
            build_player_agent(profile_a),  # seat 0 (team 0)
            build_player_agent(profile_b),  # seat 1 (team 1)
            build_player_agent(profile_a),  # seat 2 (team 0)
            build_player_agent(profile_b),  # seat 3 (team 1)
        ]
        game = Game(agents, level=2, agent_timeout_ms=agent_timeout_ms)
        try:
            result = game.play_game()
        except Exception as e:
            return GameRecord(seed=seed, winning_team=-1, rounds_played=0,
                              error=f"{type(e).__name__}: {e}")
        return GameRecord(seed=seed, winning_team=result.winning_team,
                          rounds_played=result.rounds_played)

    games: List[GameRecord] = []
    if max_workers <= 1:
        for i in range(n_games):
            games.append(play_one(i))
            if progress_cb:
                aw = sum(1 for g in games if g.winning_team == 0)
                progress_cb(i + 1, n_games,
                            f"第 {i + 1}/{n_games} 局 · 种子 {base_seed + i} · A {aw} : B {i + 1 - aw}")
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = [ex.submit(play_one, i) for i in range(n_games)]
            completed = 0
            for fut in as_completed(futures):
                games.append(fut.result())
                completed += 1
                if progress_cb:
                    aw = sum(1 for g in games if g.winning_team == 0)
                    progress_cb(completed, n_games,
                                f"已完成 {completed}/{n_games} 局 · A {aw} : B {completed - aw}")

    games.sort(key=lambda g: g.seed)
    a_wins = sum(1 for g in games if g.winning_team == 0)
    b_wins = sum(1 for g in games if g.winning_team == 1)
    errors = sum(1 for g in games if g.winning_team == -1)
    return MatchResult(a=profile_a, b=profile_b, n_games=n_games,
                       a_wins=a_wins, b_wins=b_wins, errors=errors,
                       games=games, elapsed_s=time.time() - t0)


def run_round_robin(profiles: List[dict], n_games: int = 10, base_seed: int = 0,
                    agent_timeout_ms: float = 75000, max_workers: int = 1,
                    progress_cb=None) -> dict:
    """Run a round-robin across all profiles and return an Elo ranking.

    Returns ``{"ranking": [{model, rating, wins, losses}], "matches": [...]}``.
    ``progress_cb(done, total, message)`` is called after each match.
    """
    ratings = {i: 1500.0 for i in range(len(profiles))}
    wins = {i: 0 for i in range(len(profiles))}
    losses = {i: 0 for i in range(len(profiles))}
    matches = []

    n_pairs = len(profiles) * (len(profiles) - 1) // 2
    done = 0
    for i in range(len(profiles)):
        for j in range(i + 1, len(profiles)):
            match = run_match(profiles[i], profiles[j], n_games=n_games,
                              base_seed=base_seed + (i * len(profiles) + j) * n_games,
                              agent_timeout_ms=agent_timeout_ms,
                              max_workers=max_workers)
            # Elo from completed games only
            completed = match.a_wins + match.b_wins
            if completed > 0:
                score_i = match.a_wins / completed
                ratings[i] = elo_update(ratings[i], ratings[j], score_i)
                ratings[j] = elo_update(ratings[j], ratings[i], 1 - score_i)
            wins[i] += match.a_wins
            losses[i] += match.b_wins
            wins[j] += match.b_wins
            losses[j] += match.a_wins
            matches.append(match.to_dict())

            done += 1
            if progress_cb:
                na = profiles[i].get('label', profiles[i].get('id', '?'))
                nb = profiles[j].get('label', profiles[j].get('id', '?'))
                progress_cb(done, n_pairs, f"对局 {done}/{n_pairs} · {na} vs {nb}")

    order = sorted(range(len(profiles)), key=lambda k: -ratings[k])
    ranking = [
        {"model": profiles[k], "rating": round(ratings[k], 1),
         "wins": wins[k], "losses": losses[k]}
        for k in order
    ]
    return {"ranking": ranking, "matches": matches}
