"""Multiprocess self-play tests (stage-2 performance milestone).

Coverage strategy:
  - The worker function ``_play_one_process`` and its per-seed determinism
    are verified IN-PROCESS (no pool) — always runs, including in sandboxed
    environments that deny OS pipes.
  - The real ProcessPoolExecutor path is verified only when the environment
    allows creating multiprocessing pipes (Windows DSH sandbox denies them;
    normal shells do not). Skipped there with a clear reason — run these in
    a plain terminal: ``pytest tests/test_selfplay_mp.py -v``.
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from guandan.eval.selfplay import (  # noqa: E402
    GameRecord,
    _play_one_process,
    run_match,
)

GUARD_S = 240  # hard wall clock for pool-based tests (spawn is slow)


def _pool_available() -> bool:
    """True if this environment can create multiprocessing pipes."""
    try:
        from concurrent.futures import ProcessPoolExecutor
        ex = ProcessPoolExecutor(max_workers=1)
        ex.shutdown(wait=False)
        return True
    except (PermissionError, OSError):
        return False


POOL_OK = _pool_available()

BLIND_A = {"id": "blind"}
BLIND_B = {"id": "blind"}


def test_worker_function_matches_sequential_run():
    """_play_one_process(seed=i) reproduces run_match(max_workers=1) results
    exactly — the determinism contract the pool relies on."""
    seq = run_match(BLIND_A, BLIND_B, n_games=3, base_seed=1000,
                    agent_timeout_ms=75000, max_workers=1)
    assert seq.errors == 0
    for g in seq.games:
        rec = _play_one_process((g.seed, BLIND_A, BLIND_B, 75000.0))
        assert rec == g, f"seed {g.seed}: worker {rec} != sequential {g}"
    print("  PASS worker matches sequential (3 seeds)")


def test_worker_crash_isolated_in_record():
    """A broken profile (None) raises inside the worker — the error is
    captured as a GameRecord (winning_team=-1) instead of propagating.

    Note: an unknown decider id does NOT crash — registry.create_agent_for_player
    silently falls back to its default. A non-dict profile is the reliable
    crash trigger.
    """
    bad = None  # build_player_agent(None) -> AttributeError
    rec = _play_one_process((2000, BLIND_A, bad, 75000.0))
    assert isinstance(rec, GameRecord)
    assert rec.winning_team == -1
    assert rec.seed == 2000
    assert rec.error, "expected a non-empty error message"
    assert rec.rounds_played == 0
    print(f"  PASS worker crash isolated (error: {rec.error[:60]}...)")


def test_match_with_bad_profile_records_errors_sequentially():
    """Match-level isolation (sequential path): bad-profile games become
    error records; the match completes with a well-formed result."""
    res = run_match(BLIND_A, None, n_games=2,
                    base_seed=3000, agent_timeout_ms=75000, max_workers=1)
    assert res.errors == 2
    assert len(res.games) == 2
    assert res.a_wins == 0 and res.b_wins == 0
    print("  PASS sequential bad-profile isolation")


@pytest.mark.skipif(not POOL_OK, reason="environment denies multiprocessing "
                                          "pipes (sandboxed); run in a normal shell")
class TestRealProcessPool:
    """Full-pool tests — the actual ProcessPoolExecutor fan-out."""

    def test_multiprocess_match_deterministic(self):
        t0 = time.time()
        seq = run_match(BLIND_A, BLIND_B, n_games=4, base_seed=1000,
                        agent_timeout_ms=75000, max_workers=1)
        par = run_match(BLIND_A, BLIND_B, n_games=4, base_seed=1000,
                        agent_timeout_ms=75000, max_workers=2)
        assert seq.errors == 0 and par.errors == 0
        for g_s, g_p in zip(sorted(seq.games, key=lambda g: g.seed),
                            sorted(par.games, key=lambda g: g.seed)):
            assert g_s == g_p, f"seed {g_s.seed}: {g_s} != {g_p}"
        assert seq.a_wins == par.a_wins
        assert time.time() - t0 < GUARD_S, "pool match guard exceeded"
        print(f"  PASS pool determinism (seq {seq.elapsed_s:.1f}s vs "
              f"par {par.elapsed_s:.1f}s)")

    def test_multiprocess_bad_profile_isolated(self):
        res = run_match(BLIND_A, None, n_games=4,
                        base_seed=2000, agent_timeout_ms=75000, max_workers=2)
        assert res.errors == 4, f"expected 4 error records, got {res.errors}"
        assert len(res.games) == 4
        print(f"  PASS pool crash isolation (errors={res.errors})")


if __name__ == "__main__":
    test_worker_function_matches_sequential_run()
    test_worker_crash_isolated_in_record()
    test_match_with_bad_profile_records_errors_sequentially()
    if POOL_OK:
        TestRealProcessPool().test_multiprocess_match_deterministic()
        TestRealProcessPool().test_multiprocess_bad_profile_isolated()
    else:
        print("(pool tests skipped: no multiprocessing pipes here)")
    print("OK")
