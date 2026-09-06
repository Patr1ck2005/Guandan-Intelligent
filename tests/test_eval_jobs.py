"""Eval Lab job-management hardening tests (stage-2.3).

Drives eval_api internals directly (no Flask client): cap eviction,
TTL expiry, stall watchdog, detail truncation, cancellation policy.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import guandan.eval.eval_api as E  # noqa: E402


def setup_function(fn):
    E._jobs.clear()


def teardown_function(fn):
    E._jobs.clear()


def _inject_job(status="running", started_at=None, finished_at=None,
                heartbeat=None, kind="benchmark"):
    """Insert a job dict directly — bypasses _new_job's single-active
    cancellation policy so we can unit-test _cleanup_jobs in isolation."""
    jid = f"test-{len(E._jobs)}-{int(time.time() * 1000) % 100000}"
    now = time.time()
    E._jobs[jid] = {"status": status, "progress": 0.0, "message": "",
                    "detail": {}, "cancel": False, "result": None, "error": None,
                    "kind": kind,
                    "heartbeat": heartbeat if heartbeat is not None else now,
                    "started_at": started_at if started_at is not None else now,
                    "finished_at": finished_at}
    return jid


def _make_terminal(jid, finished_at, status="done"):
    j = E._jobs[jid]
    j["status"] = status
    j["finished_at"] = finished_at


def test_job_cap_evicts_terminal_not_running():
    """Beyond _JOB_LIMIT, oldest terminal jobs go first; running survive."""
    running_jid = _inject_job(status="running")
    oldest_jid = _inject_job(status="done", finished_at=time.time() - 60)
    newest_jid = None
    for i in range(58):  # total terminal = 59 + this loop
        newest_jid = _inject_job(status="done", finished_at=time.time() - (58 - i))
    assert len(E._jobs) == 60
    E._cleanup_jobs()
    assert len(E._jobs) <= E._JOB_LIMIT
    assert running_jid in E._jobs, "running job must never be evicted"
    assert oldest_jid not in E._jobs, "oldest terminal job must be evicted first"
    assert newest_jid in E._jobs, "newest terminal job must be kept"
    print(f"  PASS cap eviction ({len(E._jobs)} jobs kept)")


def test_ttl_expires_terminal_only():
    """Terminal job past TTL is dropped; a running job of the same age stays."""
    old = time.time() - (E._JOB_TTL + 60)
    jid_old_done = _inject_job(status="done", started_at=old, finished_at=old)
    jid_old_running = _inject_job(status="running", started_at=old,
                                  heartbeat=time.time())
    E._cleanup_jobs()
    assert jid_old_done not in E._jobs, "terminal job past TTL must expire"
    assert jid_old_running in E._jobs, "running job must NOT be TTL-expired"
    print("  PASS TTL expiry terminal-only")


def test_stall_watchdog_marks_and_recovers():
    """Running job with stale heartbeat -> 'stalled'; fresh heartbeat stays;
    a stalled job that reports progress again returns to 'running'."""
    jid = _inject_job(status="running",
                      heartbeat=time.time() - (E._STALL_AFTER_S + 30))
    jid_fresh = _inject_job(status="running", heartbeat=time.time())
    E._cleanup_jobs()
    assert E._jobs[jid]["status"] == "stalled"
    assert E._jobs[jid_fresh]["status"] == "running"
    # recovery: progress resets status
    E._set_progress(jid, 0.5, "又有进展了")
    assert E._jobs[jid]["status"] == "running"
    print("  PASS stall watchdog mark + recovery")


def test_detail_truncation_over_limit():
    """Oversized live detail is replaced by a stub and flagged; normal
    details pass through untouched."""
    jid = E._new_job("benchmark")
    big = {"matrix": [[True, None]] * 50000}  # way over 256KB serialized
    E._set_detail(jid, big)
    d = E._jobs[jid]["detail"]
    assert d.get("truncated") is True
    assert d.get("keys") == ["matrix"]
    assert E._jobs[jid]["detail_truncated"] is True
    # normal detail untouched
    small = {"game": 1, "n_games": 2, "scores": [0, 1]}
    E._set_detail(jid, small)
    assert E._jobs[jid]["detail"] == small
    assert E._jobs[jid]["detail_truncated"] is True  # flag sticky for the job
    print("  PASS detail truncation")


def test_new_job_cancels_running_single_active():
    """Single-active policy: starting a new job cancels running ones."""
    first = E._new_job("match")
    second = E._new_job("benchmark")
    assert E._jobs[first]["status"] == "cancelling"
    assert E._jobs[first]["cancel"] is True
    assert E._jobs[second]["status"] == "running"
    print("  PASS single-active cancellation")


def test_spawn_records_finished_at():
    """_spawn target records finished_at on all terminal outcomes."""
    def wait_terminal(jid, timeout=5):
        deadline = time.time() + timeout
        # finished_at is set in a finally AFTER the status flips — poll for
        # it (or an error state) directly to avoid the flip/poll race.
        while time.time() < deadline:
            j = E._jobs[jid]
            if j["finished_at"] is not None or j["status"] == "error":
                return
            time.sleep(0.02)

    jid = E._new_job("benchmark")
    E._spawn(jid, lambda p, l: {"ok": True})
    wait_terminal(jid)
    assert E._jobs[jid]["status"] == "done"
    assert E._jobs[jid]["finished_at"] is not None
    # error path
    jid2 = E._new_job("match")
    def boom(p, l):
        raise ValueError("boom")
    E._spawn(jid2, boom)
    wait_terminal(jid2)
    assert E._jobs[jid2]["status"] == "error"
    assert E._jobs[jid2]["finished_at"] is not None
    assert "boom" in (E._jobs[jid2]["error"] or "")
    print("  PASS spawn finished_at recording")


if __name__ == "__main__":
    for fn in [test_job_cap_evicts_terminal_not_running,
               test_ttl_expires_terminal_only,
               test_stall_watchdog_marks_and_recovers,
               test_detail_truncation_over_limit,
               test_new_job_cancels_running_single_active,
               test_spawn_records_finished_at]:
        setup_function(fn)
        fn()
        teardown_function(fn)
    print("OK")
