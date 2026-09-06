"""History API + export endpoint tests (stage 3.2 / 3.3).

Uses the Flask test client against the real blueprint with the store
pointed at a temp DB.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from guandan.eval import store  # noqa: E402
from guandan.ui.web.app import app  # noqa: E402


@pytest.fixture()
def client(tmp_path):
    store.reset_for_tests(str(tmp_path / "hist.db"))
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    store.reset_for_tests()


def _seed_runs():
    bench = {"results": [1] * 12, "summary": [
        {"model": "blind", "model_name": "blind", "accuracy": 66.7,
         "correct": 8, "total": 12, "avg_ms": 0.3}]}
    rr = {"ranking": [{"model": {"id": "blind"}, "rating": 1510.0,
                       "wins": 5, "losses": 5}], "matches": []}
    id1 = store.store_run("benchmark", {"models": [{"id": "blind"}]}, bench, seed=0)
    id2 = store.store_run("round_robin", {"models": ["blind"]}, rr, seed=1)
    return id1, id2


def test_history_list_and_detail(client):
    id1, id2 = _seed_runs()
    r = client.get("/api/eval/history?limit=10")
    assert r.status_code == 200
    runs = r.get_json()["runs"]
    assert [x["id"] for x in runs] == [id2, id1]  # newest first
    # kind filter
    r2 = client.get("/api/eval/history?kind=benchmark")
    assert [x["id"] for x in r2.get_json()["runs"]] == [id1]
    # detail
    r3 = client.get(f"/api/eval/history/{id1}")
    assert r3.status_code == 200
    assert r3.get_json()["kind"] == "benchmark"
    # missing
    r4 = client.get("/api/eval/history/999999")
    assert r4.status_code == 404
    print("  PASS history list/detail/404")


def test_export_json_csv_html(client):
    id1, _ = _seed_runs()
    # json
    rj = client.get(f"/api/eval/export/{id1}?format=json")
    assert rj.status_code == 200
    assert rj.get_json()["id"] == id1
    # csv
    rc = client.get(f"/api/eval/export/{id1}?format=csv")
    assert rc.status_code == 200
    assert rc.mimetype == "text/csv"
    lines = rc.get_data(as_text=True).strip().splitlines()
    assert lines[0].startswith("model_id,model_name")
    assert "blind" in lines[1]
    # html — self-contained, contains params + summary + commit
    rh = client.get(f"/api/eval/export/{id1}?format=html")
    assert rh.status_code == 200
    html = rh.get_data(as_text=True)
    assert "掼蛋评估报告" in html
    assert "参数快照" in html and "结果摘要" in html
    assert "<script src=" not in html, "report must be self-contained"
    # unknown format
    r404 = client.get(f"/api/eval/export/{id1}?format=xml")
    assert r404.status_code == 400
    print("  PASS export json/csv/html")


def test_export_missing_run(client):
    r = client.get("/api/eval/export/424242?format=json")
    assert r.status_code == 404
    print("  PASS export 404")
