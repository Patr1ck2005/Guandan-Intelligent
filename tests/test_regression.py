"""Regression baseline module tests (stage 3.5) — pure comparison logic."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from guandan.eval.regression import (  # noqa: E402
    collect_metrics,
    compare_metrics,
    _match_share,
)


def test_collect_metrics_shapes():
    bench = {"summary": [
        {"model": "blind", "accuracy": 66.7, "wilson": [42.3, 84.6]},
        {"model": "mc", "accuracy": 50.0, "wilson": [25.4, 74.6]},
    ]}
    match = {"a_wins": 6, "b_wins": 4, "errors": 0}
    m = collect_metrics(bench, match)
    assert m["benchmark"]["blind"]["accuracy"] == 66.7
    assert m["match"]["a_share"] == 60.0
    assert m["match"]["n_games"] == 10
    print("  PASS collect_metrics")


def test_match_share_empty():
    s = _match_share({"a_wins": 0, "b_wins": 0, "errors": 2})
    assert s["a_share"] is None and s["n_games"] == 0
    print("  PASS match share empty")


def test_compare_green():
    base = {"benchmark": {"blind": {"accuracy": 66.7}},
            "match": {"a_share": 60.0}}
    cur = {"benchmark": {"blind": {"accuracy": 70.0}},
           "match": {"a_share": 65.0}}
    r = compare_metrics(cur, base)
    assert r["verdict"] == "green"
    assert r["metrics_checked"] == 2
    print("  PASS green verdict")


def test_compare_accuracy_breach():
    base = {"benchmark": {"blind": {"accuracy": 66.7}}, "match": {}}
    cur = {"benchmark": {"blind": {"accuracy": 50.0}}, "match": {}}
    r = compare_metrics(cur, base)
    assert r["verdict"] == "red"
    assert r["breaches"][0]["metric"] == "benchmark.blind.accuracy"
    assert r["breaches"][0]["delta_pp"] == -16.7
    print("  PASS accuracy breach (rule break reproduces as red)")


def test_compare_win_share_breach():
    base = {"benchmark": {}, "match": {"a_share": 50.0}}
    cur = {"benchmark": {}, "match": {"a_share": 70.0}}
    r = compare_metrics(cur, base)
    assert r["verdict"] == "red"
    assert r["breaches"][0]["metric"] == "match.a_share"
    print("  PASS win-share breach")


def test_compare_absent_baseline_not_breach():
    """A new metric with no baseline entry is skipped, not failed."""
    base = {"benchmark": {}, "match": {}}
    cur = {"benchmark": {"mc": {"accuracy": 50.0}}, "match": {"a_share": 60.0}}
    r = compare_metrics(cur, base)
    assert r["verdict"] == "green"
    assert r["metrics_checked"] == 0
    print("  PASS absent baseline metrics skipped")


def test_compare_empty_inputs():
    r = compare_metrics({}, {})
    assert r["verdict"] == "green"
    print("  PASS empty inputs")


if __name__ == "__main__":
    test_collect_metrics_shapes()
    test_match_share_empty()
    test_compare_green()
    test_compare_accuracy_breach()
    test_compare_win_share_breach()
    test_compare_absent_baseline_not_breach()
    test_compare_empty_inputs()
    print("OK")
