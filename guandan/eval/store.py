"""SQLite-backed evaluation history store.

Persists every finished eval-lab job (benchmark / match / round_robin /
tuner / regression) so experiments accumulate instead of evaporating with
the in-memory job dict. Zero external dependencies (stdlib sqlite3).

Design notes:
  - Single connection guarded by a process-wide lock; WAL journal so the
    Flask request threads reading history never block on a job thread
    writing a result.
  - Store failures NEVER break the job that produced the result —
    store_run() swallows and logs; the eval lab degrades to in-memory.
  - The DB lives in data/eval_history.db (gitignored).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import List, Optional

from guandan.logging import game_logger

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    epoch_s REAL NOT NULL,
    kind TEXT NOT NULL,
    params_json TEXT NOT NULL,
    seed INTEGER,
    result_summary_json TEXT,
    git_commit TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS run_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    model_id TEXT NOT NULL,
    model_name TEXT,
    accuracy REAL,
    elo REAL,
    wins INTEGER,
    losses INTEGER,
    avg_ms REAL,
    extra_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_kind ON runs(kind);
CREATE INDEX IF NOT EXISTS idx_runs_epoch ON runs(epoch_s);
CREATE INDEX IF NOT EXISTS idx_run_models_run ON run_models(run_id);
"""

_DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "eval_history.db")
_conn: Optional[sqlite3.Connection] = None
_lock = threading.Lock()


def _get_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            path = db_path or os.environ.get("GUANDAN_EVAL_DB") or _DEFAULT_DB
            os.makedirs(os.path.dirname(path), exist_ok=True)
            _conn = sqlite3.connect(path, check_same_thread=False)
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA synchronous=NORMAL")
            _conn.executescript(_SCHEMA)
            _conn.commit()
        return _conn


def reset_for_tests(db_path: Optional[str] = None):
    """Close and drop the cached connection (tests point it at a tmp file)."""
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
        _conn = None
    if db_path:
        os.environ["GUANDAN_EVAL_DB"] = db_path
    else:
        os.environ.pop("GUANDAN_EVAL_DB", None)


def _git_commit() -> Optional[str]:
    try:
        import subprocess
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))))
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


def store_run(kind: str, params: dict, result: dict,
              seed: Optional[int] = None, notes: Optional[str] = None,
              db_path: Optional[str] = None) -> Optional[int]:
    """Persist one finished job. Returns the run id (or None on failure).

    Never raises: a broken store must not fail the eval job that produced
    the result.
    """
    try:
        conn = _get_conn(db_path)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        summary = _extract_summary(kind, result)
        git_sha = _git_commit()  # subprocess (≤5s) must not hold the DB lock
        with _lock:
            cur = conn.execute(
                "INSERT INTO runs(ts_utc, epoch_s, kind, params_json, seed,"
                " result_summary_json, git_commit, notes)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (ts, time.time(), kind, json.dumps(params, ensure_ascii=False,
                                                   default=str),
                 seed, json.dumps(summary, ensure_ascii=False, default=str),
                 git_sha, notes))
            run_id = cur.lastrowid
            rows = _extract_model_rows(kind, result)
            conn.executemany(
                "INSERT INTO run_models(run_id, model_id, model_name,"
                " accuracy, elo, wins, losses, avg_ms, extra_json)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                [(run_id, r["model_id"], r.get("model_name"),
                  r.get("accuracy"), r.get("elo"), r.get("wins"),
                  r.get("losses"), r.get("avg_ms"),
                  json.dumps(r.get("extra"), ensure_ascii=False, default=str)
                  if r.get("extra") else None) for r in rows])
            conn.commit()
        return run_id
    except Exception as e:
        try:
            game_logger.warning("eval_store_failed", error=str(e))
        except Exception:
            pass
        return None


def _extract_summary(kind: str, result: dict) -> dict:
    """Compact per-kind summary stored alongside the full result ref."""
    if not isinstance(result, dict):
        return {"raw": str(result)[:500]}
    if kind == "benchmark":
        return {"summary": result.get("summary", []),
                "n_results": len(result.get("results", []))}
    if kind in ("match",):
        return {"a": result.get("a"), "b": result.get("b"),
                "n_games": result.get("n_games"),
                "a_wins": result.get("a_wins"), "b_wins": result.get("b_wins"),
                "errors": result.get("errors"),
                "elapsed_s": result.get("elapsed_s")}
    if kind == "round_robin":
        return {"ranking": result.get("ranking", [])}
    # tuner / regression / generic: store everything JSON-safe
    try:
        json.dumps(result, ensure_ascii=False, default=str)
        return result
    except (TypeError, ValueError):
        return {"raw": str(result)[:500]}


def _extract_model_rows(kind: str, result: dict) -> List[dict]:
    rows: List[dict] = []
    if kind == "benchmark":
        for s in result.get("summary", []):
            rows.append({"model_id": s.get("model"), "model_name": s.get("model_name"),
                         "accuracy": s.get("accuracy"), "avg_ms": s.get("avg_ms"),
                         "extra": {"correct": s.get("correct"), "total": s.get("total")}})
    elif kind == "match":
        n = max(1, (result.get("a_wins") or 0) + (result.get("b_wins") or 0))
        rows.append({"model_id": "A", "model_name": (result.get("a") or {}).get("id"),
                     "wins": result.get("a_wins"), "losses": result.get("b_wins"),
                     "accuracy": round(100.0 * (result.get("a_wins") or 0) / n, 1)})
        rows.append({"model_id": "B", "model_name": (result.get("b") or {}).get("id"),
                     "wins": result.get("b_wins"), "losses": result.get("a_wins"),
                     "accuracy": round(100.0 * (result.get("b_wins") or 0) / n, 1)})
    elif kind == "round_robin":
        for r in result.get("ranking", []):
            rows.append({"model_id": (r.get("model") or {}).get("id"),
                         "model_name": (r.get("model") or {}).get("id"),
                         "elo": r.get("rating"), "wins": r.get("wins"),
                         "losses": r.get("losses")})
    return rows


def list_runs(kind: Optional[str] = None, limit: int = 50,
              db_path: Optional[str] = None) -> List[dict]:
    """Most recent runs (newest first), with their per-model rows."""
    try:
        conn = _get_conn(db_path)
        with _lock:
            if kind:
                cur = conn.execute(
                    "SELECT id, ts_utc, kind, params_json, seed,"
                    " result_summary_json, git_commit, notes FROM runs"
                    " WHERE kind=? ORDER BY id DESC LIMIT ?", (kind, limit))
            else:
                cur = conn.execute(
                    "SELECT id, ts_utc, kind, params_json, seed,"
                    " result_summary_json, git_commit, notes FROM runs"
                    " ORDER BY id DESC LIMIT ?", (limit,))
            out = []
            for (rid, ts, k, pj, seed, rj, commit, notes) in cur.fetchall():
                models = conn.execute(
                    "SELECT model_id, model_name, accuracy, elo, wins, losses,"
                    " avg_ms, extra_json FROM run_models WHERE run_id=?"
                    " ORDER BY id", (rid,)).fetchall()
                out.append({
                    "id": rid, "ts_utc": ts, "kind": k,
                    "params": json.loads(pj) if pj else {},
                    "seed": seed,
                    "summary": json.loads(rj) if rj else {},
                    "git_commit": commit, "notes": notes,
                    "models": [{
                        "model_id": m[0], "model_name": m[1],
                        "accuracy": m[2], "elo": m[3], "wins": m[4],
                        "losses": m[5], "avg_ms": m[6],
                        "extra": json.loads(m[7]) if m[7] else {},
                    } for m in models],
                })
            return out
    except Exception as e:
        try:
            game_logger.warning("eval_store_read_failed", error=str(e))
        except Exception:
            pass
        return []


def get_run(run_id: int, db_path: Optional[str] = None) -> Optional[dict]:
    """One full run (same shape as list_runs entries, single item)."""
    try:
        conn = _get_conn(db_path)
        with _lock:
            cur = conn.execute(
                "SELECT id, ts_utc, kind, params_json, seed,"
                " result_summary_json, git_commit, notes FROM runs WHERE id=?",
                (run_id,))
            row = cur.fetchone()
            if not row:
                return None
            models = conn.execute(
                "SELECT model_id, model_name, accuracy, elo, wins, losses,"
                " avg_ms, extra_json FROM run_models WHERE run_id=? ORDER BY id",
                (run_id,)).fetchall()
        return {
            "id": row[0], "ts_utc": row[1], "kind": row[2],
            "params": json.loads(row[3]) if row[3] else {},
            "seed": row[4],
            "summary": json.loads(row[5]) if row[5] else {},
            "git_commit": row[6], "notes": row[7],
            "models": [{
                "model_id": m[0], "model_name": m[1], "accuracy": m[2],
                "elo": m[3], "wins": m[4], "losses": m[5], "avg_ms": m[6],
                "extra": json.loads(m[7]) if m[7] else {},
            } for m in models],
        }
    except Exception as e:
        try:
            game_logger.warning("eval_store_read_failed", error=str(e))
        except Exception:
            pass
        return None


def get_baseline(kind: str = "regression",
                 db_path: Optional[str] = None) -> Optional[dict]:
    """The most recent run explicitly saved as a baseline (notes=baseline:)."""
    try:
        conn = _get_conn(db_path)
        with _lock:
            cur = conn.execute(
                "SELECT id FROM runs WHERE kind=? AND notes LIKE 'baseline:%'"
                " ORDER BY id DESC LIMIT 1", (kind,))
            row = cur.fetchone()
        return get_run(row[0], db_path) if row else None
    except Exception as e:
        try:
            game_logger.warning("eval_store_read_failed", error=str(e))
        except Exception:
            pass
        return None


def save_baseline(run_id: int, kind: str = "regression",
                  db_path: Optional[str] = None) -> bool:
    """Mark an existing run as the baseline (notes prefix 'baseline:')."""
    try:
        conn = _get_conn(db_path)
        with _lock:
            cur = conn.execute("SELECT kind FROM runs WHERE id=?", (run_id,))
            row = cur.fetchone()
            if not row:
                return False
            conn.execute("UPDATE runs SET notes=? WHERE id=?",
                         (f"baseline:{kind}", run_id))
            conn.commit()
        return True
    except Exception as e:
        try:
            game_logger.warning("eval_store_failed", error=str(e))
        except Exception:
            pass
        return False
