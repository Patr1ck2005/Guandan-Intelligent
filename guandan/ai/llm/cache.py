"""Persistent response cache for LLM calls.

Re-running the same benchmark/decision point should not re-spend tokens,
so raw `client.chat()` responses are cached keyed by the full request content
(model + mode + temperature + max_tokens + user message).

The cache is a JSON file (gitignored via the existing `*.json` rule) and is
best-effort: a read/write failure never blocks the game.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from typing import Optional


class LLMResponseCache:
    """Thread-safe cache of raw LLM responses, persisted to a local JSON file."""

    def __init__(self, path: str = ".llm_cache.json"):
        self._path = path
        self._data: dict = {}
        self._lock = threading.Lock()
        self._loaded = False

    def _load(self):
        if self._loaded:
            return
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}
        self._loaded = True

    def _save(self):
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f)
        except OSError:
            pass  # cache is best-effort; never block on a write failure

    @staticmethod
    def make_key(
        model: str, mode: str, temperature: float, max_tokens: int, user_msg: str
    ) -> str:
        payload = json.dumps(
            {
                "model": model,
                "mode": mode,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "user_msg": user_msg,
            },
            sort_keys=True,
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[dict]:
        with self._lock:
            self._load()
            return self._data.get(key)

    def set(self, key: str, response: dict):
        with self._lock:
            self._load()
            self._data[key] = response
            self._save()

    def clear(self):
        with self._lock:
            self._data = {}
            self._loaded = True
            self._save()


_cache_singleton: Optional[LLMResponseCache] = None


def get_response_cache() -> LLMResponseCache:
    global _cache_singleton
    if _cache_singleton is None:
        _cache_singleton = LLMResponseCache()
    return _cache_singleton
