"""Self-play harness — headless match and round-robin evaluation.

Reuses ``Game.play_game()`` to run full 2v2 games and reports win rates / Elo.
Every result records the exact agent profiles + seed used, so results are
honest and reproducible — no hidden parameter reductions.
Individual game errors (e.g. an agent bug) are recorded rather than aborting
the whole match.

Parallelism: ``max_workers=1`` runs sequentially in-process with per-move
live dashboard callbacks. ``max_workers>1`` fans games out to a
``ProcessPoolExecutor`` (Windows spawn semantics — the worker is a
module-level function whose args are plain-picklable, agents are rebuilt
inside each child). Each game is seeded independently, so per-game results
are IDENTICAL between sequential and parallel runs for the same seed set.
The trade-off: in multiprocess mode the live dashboard updates once per
completed game (move-level callbacks cannot cross process boundaries).
"""

from __future__ import annotations

import random
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

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


class JobInterrupted(Exception):
    """Raised when progress_cb signals cancellation — aborts the match loop."""


def _play_one_process(args: tuple) -> GameRecord:
    """Multiprocess worker: play ONE game in a fresh process.

    Module-level so it is picklable under Windows spawn semantics. Args are
    plain data: ``(seed, profile_a, profile_b, agent_timeout_ms)``. The game
    uses an ISOLATED RNG seeded with ``seed`` (not the global random state),
    so per-game results are identical to the sequential run for the same
    seed regardless of process scheduling or caller RNG state.

    Any failure (agent construction, game crash) is contained: the record
    reports the error and the match keeps going.
    """
    seed, profile_a, profile_b, agent_timeout_ms = args
    try:
        # Two deterministic sources: (1) an isolated RNG drives the deal and
        # first-leader draw; (2) random.seed() makes AGENT-level global RNG
        # consumption (BlindAgent pass rolls, MC sampling shuffles)
        # deterministic per game. Together: same seed -> same game.
        rng = random.Random(seed)
        random.seed(seed)
        agents = [
            build_player_agent(profile_a),
            build_player_agent(profile_b),
            build_player_agent(profile_a),
            build_player_agent(profile_b),
        ]
        game = Game(agents, level=2, agent_timeout_ms=agent_timeout_ms, rng=rng)
        result = game.play_game()
    except Exception as e:
        return GameRecord(seed=seed, winning_team=-1, rounds_played=0,
                          error=f"{type(e).__name__}: {e}")
    return GameRecord(seed=seed, winning_team=result.winning_team,
                      rounds_played=result.rounds_played)


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
              max_workers: int = 1, progress_cb=None, live_cb=None) -> MatchResult:
    """Run a 2v2 match (A seats 0/2, B seats 1/3) over ``n_games`` games.

    ``max_workers=1`` (default) is sequential in-process with per-move live
    dashboard callbacks. ``max_workers>1`` fans games out to a process pool —
    each game is seeded with ``base_seed + i`` and seeds its own RNG, so
    per-game results are identical to the sequential run (fully
    seed-reproducible). Live callbacks degrade to per-game granularity.

    ``live_cb(detail_dict)`` is called with a dashboard snapshot (per-move in
    sequential mode; per-game in multiprocess mode).
    """
    t0 = time.time()
    _lock = threading.Lock()
    # Progress model: fraction = (completed games + window) / n_games, where the
    # window grows with the current game's move count (capped at 0.95 so one
    # game never fills the bar). Move counts are estimates; game-completion
    # callbacks are authoritative, so the bar advances smoothly inside a long
    # game and lands exactly on each game boundary. Strictly monotonic.
    _st = {"completed": 0, "window": 0.0}
    _cnt = {"n": 0}
    MOVES_PER_GAME_ESTIMATE = 250  # a long blind game can exceed 150 moves
    MAX_HIST = 60  # sparkline window (recent move durations, ms)
    _live = {"t_last": None, "last_move_ms": 0.0, "move_ms_sum": 0.0,
             "think_ms": [0.0, 0.0], "hist": [], "scores": []}

    def _emit(message: str):
        if not progress_cb:
            return
        with _lock:
            frac = min((_st["completed"] + min(_st["window"], 0.95)) / n_games, 1.0)
        progress_cb(int(frac * 100), 100, message)

    def on_move(game, player: int, combo):
        """Called by Game after every play/pass so the bar moves continuously."""
        t_move = time.time()
        _cnt["n"] += 1
        with _lock:
            _st["window"] = min(_cnt["n"] / MOVES_PER_GAME_ESTIMATE, 0.95)
            last_ms = 0.0 if _live["t_last"] is None else (t_move - _live["t_last"]) * 1000
            _live["t_last"] = t_move
            _live["last_move_ms"] = last_ms
            _live["move_ms_sum"] += last_ms
            team = player % 2  # seats 0/2 = A(team0), 1/3 = B(team1)
            _live["think_ms"][team] += last_ms
            _live["hist"].append(last_ms)
            if len(_live["hist"]) > MAX_HIST:
                del _live["hist"][:len(_live["hist"]) - MAX_HIST]
        if live_cb:
            table = game.state.table
            live_cb({
                "game": min(_st["completed"] + 1, n_games), "n_games": n_games,
                "move": _cnt["n"], "trick": game.state.trick_number,
                "round_number": game.state.round_number,
                "hand_sizes": [len(h) for h in game.state.hands],
                "last_player": player,
                "last_action": "pass" if combo is None else "play",
                "last_combo_type": "" if combo is None else combo.combo_type.name,
                "last_combo_cards": [] if combo is None else [c.display for c in combo.cards],
                "last_move_ms": round(last_ms, 1),
                "avg_move_ms": round(_live["move_ms_sum"] / max(1, _cnt["n"]), 1),
                "moves_per_sec": round(_cnt["n"] / max(1e-9, t_move - t0), 2),
                "think_ms": [round(_live["think_ms"][0]), round(_live["think_ms"][1])],
                "move_ms_hist": list(_live["hist"]),
                "trick_leader": table.last_played_player,
                "scores": list(_live["scores"]),
            })
        if _cnt["n"] % 5 == 0:
            with _lock:
                done = _st["completed"]
            _emit(f"第 {_cnt['n']} 步 · 局 {done + 1}/{n_games}")

    def play_one(i: int) -> GameRecord:
        seed = base_seed + i
        try:
            # Deterministic per (seed, profiles): isolated RNG for the deal +
            # first leader; random.seed() pins agent-level global RNG use.
            # The old code only did the second — the deal/starter leaked
            # the caller's ambient RNG state into results.
            rng = random.Random(seed)
            random.seed(seed)
            agents = [
                build_player_agent(profile_a),  # seat 0 (team 0)
                build_player_agent(profile_b),  # seat 1 (team 1)
                build_player_agent(profile_a),  # seat 2 (team 0)
                build_player_agent(profile_b),  # seat 3 (team 1)
            ]
            _cnt["n"] = 0
            with _lock:
                _live["t_last"] = None  # avoid inter-game gap counting as a move
            game = Game(agents, level=2, agent_timeout_ms=agent_timeout_ms,
                        on_move=on_move, rng=rng)
            result = game.play_game()
        except Exception as e:
            return GameRecord(seed=seed, winning_team=-1, rounds_played=0,
                              error=f"{type(e).__name__}: {e}")
        return GameRecord(seed=seed, winning_team=result.winning_team,
                          rounds_played=result.rounds_played)

    def _record_completion(rec: GameRecord, completed: int, label: str):
        games.append(rec)
        with _lock:
            _live["scores"].append(rec.winning_team)
            _st["completed"] = max(_st["completed"], completed)
            _st["window"] = 0.0
        aw = sum(1 for g in games if g.winning_team == 0)
        if live_cb:
            live_cb({
                "mode": "multiprocess" if max_workers > 1 else "sequential",
                "workers": max_workers,
                "game": min(completed, n_games), "n_games": n_games,
                "last_winner_team": rec.winning_team,
                "last_seed": rec.seed,
                "scores": list(_live["scores"]),
            })
        _emit(f"第 {completed}/{n_games} 局完成 · {label} A {aw} : B {completed - aw}")

    games: List[GameRecord] = []
    if max_workers <= 1:
        for i in range(n_games):
            with _lock:
                _live["scores"].append(-2)  # placeholder; patched on completion
            rec = play_one(i)
            aw_prev = sum(1 for g in games if g.winning_team == 0)
            games.append(rec)
            with _lock:
                _live["scores"][-1] = rec.winning_team
                _st["completed"] = i + 1
                _st["window"] = 0.0
            aw = sum(1 for g in games if g.winning_team == 0)
            _emit(f"第 {i + 1}/{n_games} 局完成 · A {aw} : B {i + 1 - aw}")
    else:
        workers = min(max_workers, n_games)
        pool_args = [(base_seed + i, profile_a, profile_b, agent_timeout_ms)
                     for i in range(n_games)]
        completed = 0
        ex = ProcessPoolExecutor(max_workers=workers)
        try:
            futures = [ex.submit(_play_one_process, args) for args in pool_args]
            for fut in as_completed(futures):
                rec = fut.result()
                completed += 1
                _record_completion(rec, completed, f"多进程{workers}路 ·")
            ex.shutdown(wait=True)
        except BaseException:
            # Cancellation (JobInterrupted) or a broken pool: stop scheduling
            # new games immediately instead of waiting for in-flight ones.
            ex.shutdown(wait=False, cancel_futures=True)
            raise

    games.sort(key=lambda g: g.seed)
    a_wins = sum(1 for g in games if g.winning_team == 0)
    b_wins = sum(1 for g in games if g.winning_team == 1)
    errors = sum(1 for g in games if g.winning_team == -1)
    return MatchResult(a=profile_a, b=profile_b, n_games=n_games,
                       a_wins=a_wins, b_wins=b_wins, errors=errors,
                       games=games, elapsed_s=time.time() - t0)


def run_round_robin(profiles: List[dict], n_games: int = 10, base_seed: int = 0,
                    agent_timeout_ms: float = 75000, max_workers: int = 1,
                    progress_cb=None, live_cb=None) -> dict:
    """Run a round-robin across all profiles and return an Elo ranking.

    Returns ``{"ranking": [{model, rating, wins, losses}], "matches": [...]}``.
    ``progress_cb(done, total, message)`` is called after each match (sub-match
    move progress folds into the pair's slice). ``live_cb(detail_dict)``
    forwards the current sub-match dashboard plus an Elo snapshot.
    """
    ratings = {i: 1500.0 for i in range(len(profiles))}
    wins = {i: 0 for i in range(len(profiles))}
    losses = {i: 0 for i in range(len(profiles))}
    matches = []

    n_pairs = len(profiles) * (len(profiles) - 1) // 2
    done = 0
    for i in range(len(profiles)):
        for j in range(i + 1, len(profiles)):
            na = profiles[i].get('label', profiles[i].get('id', '?'))
            nb = profiles[j].get('label', profiles[j].get('id', '?'))

            def sub_progress(sub_done, sub_total, msg):
                if progress_cb:
                    # sub_done is run_match's internal 0..100 percent; fold it
                    # into this pair's slice so the bar crawls inside a pair.
                    # If the job was cancelled, progress_cb raises
                    # JobCancelled (a JobInterrupted subclass) which aborts
                    # the sub-match and propagates up through run_round_robin.
                    progress_cb(done * 100 + max(0, min(int(sub_done), 99)),
                                n_pairs * 100,
                                f"对局 {done + 1}/{n_pairs} · {na} vs {nb} · {msg}")

            def sub_live(detail):
                if live_cb:
                    detail = dict(detail)
                    detail["rr_pair"] = f"{na} vs {nb}"
                    detail["rr_done"] = done
                    detail["rr_total"] = n_pairs
                    detail["elo"] = [{"label": profiles[k].get(
                        'label', profiles[k].get('id', '?')),
                        "rating": round(ratings[k])} for k in sorted(ratings)]
                    live_cb(detail)

            match = run_match(profiles[i], profiles[j], n_games=n_games,
                              base_seed=base_seed + (i * len(profiles) + j) * n_games,
                              agent_timeout_ms=agent_timeout_ms,
                              max_workers=max_workers,
                              progress_cb=sub_progress, live_cb=sub_live)
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
                progress_cb(done, n_pairs, f"对局 {done}/{n_pairs} · {na} vs {nb}")

    order = sorted(range(len(profiles)), key=lambda k: -ratings[k])
    ranking = [
        {"model": profiles[k], "rating": round(ratings[k], 1),
         "wins": wins[k], "losses": losses[k]}
        for k in order
    ]
    return {"ranking": ranking, "matches": matches}
