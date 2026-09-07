"""End-to-end demo (stage 5): benchmark -> auto-store -> history -> export ->
regression suite -> baseline badge. Drives the live server on :8766 with a
cookie-based opener. Read-only w.r.t. game state; writes only eval history.
"""
import json
import os
import sys
import time
import urllib.request
from http.cookiejar import CookieJar

BASE = os.environ.get("GUANDAN_BASE", "http://localhost:8766")
cj = CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def call(path, payload=None, timeout=30):
    url = BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def wait_job(jid, timeout_s=300):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        j = call(f"/api/eval/jobs/{jid}")
        if j["status"] in ("done", "error", "cancelled", "stalled"):
            return j
        time.sleep(0.7)
    raise TimeoutError(jid)


def main():
    print("== 1. benchmark job (auto-persists to SQLite) ==")
    j = wait_job(call("/api/eval/benchmark", {
        "models": [{"id": "blind"}, {"id": "mc", "num_samples": 8,
                                     "time_limit_ms": 500, "inner": "blind",
                                     "enumerator": "full"}],
        "max_scenarios": 12})["job_id"])
    assert j["status"] == "done", j
    run_id = j["run_id"]
    wilson = j["result"]["summary"][0].get("wilson")
    print(f"   job done, run_id={run_id}, top wilson CI={wilson}")

    print("== 2. history lists it ==")
    hist = call("/api/eval/history?limit=5")["runs"]
    assert any(r["id"] == run_id for r in hist), "benchmark run missing from history"
    print(f"   history has {len(hist)} runs, newest #{hist[0]['id']} "
          f"({hist[0]['kind']})")

    print("== 3. export json/csv/html ==")
    for fmt, marker in [("json", '"kind": "benchmark"'),
                        ("csv", "model_id"),
                        ("html", "掼蛋评估报告")]:
        with opener.open(f"{BASE}/api/eval/export/{run_id}?format={fmt}",
                         timeout=10) as r:
            body = r.read().decode("utf-8", "replace")
        assert marker in body, f"{fmt} export missing marker"
        print(f"   {fmt}: OK ({len(body)} bytes)")

    print("== 4. regression suite (standard, fixed seed) ==")
    j = wait_job(call("/api/eval/regression", {"match_n_games": 2,
                                               "seed": 4242})["job_id"],
                 timeout_s=240)
    assert j["status"] == "done", j
    verdict = j["result"]["verdict"]
    print(f"   regression verdict: {verdict} "
          f"(baseline_run_id={j['result']['baseline_run_id']})")

    print("== 5. save baseline + badge endpoint ==")
    reg_run = call("/api/eval/regression/latest")["run"]
    call("/api/eval/regression/baseline", {"run_id": reg_run["id"]})
    latest = call("/api/eval/regression/latest")
    print(f"   baseline saved on #{latest['run']['id']}, verdict={latest['verdict']}")

    print("== 6. tiny tuner job (2 candidates x 2 games, 60s cap) ==")
    j = wait_job(call("/api/eval/tuner", {"max_candidates": 2,
                                          "games_per_candidate": 2,
                                          "max_seconds": 60,
                                          "max_workers": 2,
                                          "seed": 42})["job_id"],
                 timeout_s=120)
    assert j["status"] == "done", j
    best = j["result"]["best"]
    print(f"   tuner done: best win_rate={best['win_rate']}, "
          f"verdict={j['result']['verdict']}, stored run_id={j['run_id']}")

    print("\nEND-TO-END DEMO: ALL GREEN")


if __name__ == "__main__":
    main()
