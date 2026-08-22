"""DeepSeek API client with retry logic and streaming support."""

from __future__ import annotations

import json
import time
from typing import List

import httpx

from .config import LLMConfig
from .tracker import TokenTracker, get_token_tracker


class LLMClientError(Exception):
    """Base exception for LLM client errors."""


class LLMAuthError(LLMClientError):
    """Raised on HTTP 401 — API key is invalid."""


class LLMRateLimitError(LLMClientError):
    """Raised on HTTP 429 — rate limit exceeded."""


class LLMTimeoutError(LLMClientError):
    """Raised when all retries are exhausted due to timeouts."""


class DeepSeekAPIClient:
    """Synchronous HTTP client for the DeepSeek API (OpenAI-compatible).

    Called from within the game engine's ThreadPoolExecutor, so blocking
    inside the thread is fine.
    """

    def __init__(self, config: LLMConfig):
        self.config = config
        self._client: httpx.Client | None = None

    @property
    def endpoint(self) -> str:
        return f"{self.config.api_base_url.rstrip('/')}/v1/chat/completions"

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(self.config.timeout_s),
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    def chat(
        self,
        messages: List[dict],
        tracker: TokenTracker | None = None,
    ) -> dict:
        """Send a chat completion request. Returns {content, usage, model}."""
        if not self.config.api_key:
            raise LLMAuthError("DEEPSEEK_API_KEY not set")

        body = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": self.config.stream,
            "stop": ["}\n\n", "]\n\n"],
        }

        last_error = None
        for attempt in range(self.config.max_retries + 1):
            try:
                if self.config.stream:
                    result = self._do_stream(body)
                else:
                    result = self._do_request(body)

                if tracker and self.config.track_tokens and result.get("usage"):
                    tracker.record(result["usage"], self.config.model)

                return result
            except LLMAuthError:
                raise
            except LLMRateLimitError as e:
                last_error = e
                if attempt < self.config.max_retries:
                    time.sleep(2**attempt)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as e:
                last_error = e
                if attempt < self.config.max_retries:
                    time.sleep(2**attempt)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    last_error = LLMRateLimitError("Rate limited")
                    if attempt < self.config.max_retries:
                        time.sleep(2**attempt)
                        continue
                elif e.response.status_code == 401:
                    raise LLMAuthError("Invalid API key (401)")
                else:
                    last_error = e
                    if attempt < self.config.max_retries:
                        time.sleep(2**attempt)

        if isinstance(last_error, (httpx.TimeoutException,)):
            raise LLMTimeoutError("All retries exhausted due to timeout")
        if isinstance(last_error, LLMRateLimitError):
            raise last_error
        raise LLMClientError(f"Request failed after {self.config.max_retries + 1} attempts: {last_error}")

    def _do_request(self, body: dict) -> dict:
        client = self._get_client()
        resp = client.post(self.endpoint, json=body)
        resp.raise_for_status()
        data = resp.json()
        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        return {
            "content": msg.get("content") or "",
            "reasoning": msg.get("reasoning_content") or "",
            "usage": data.get("usage"),
            "model": data.get("model", self.config.model),
        }

    def _do_stream(self, body: dict) -> dict:
        client = self._get_client()
        content_parts = []
        reasoning_parts = []
        usage = None

        with client.stream("POST", self.endpoint, json=body) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                delta = (chunk.get("choices", [{}])[0].get("delta") or {})
                if delta.get("content"):
                    content_parts.append(delta["content"])
                if delta.get("reasoning_content"):
                    reasoning_parts.append(delta["reasoning_content"])
                if chunk.get("usage"):
                    usage = chunk["usage"]

        return {
            "content": "".join(content_parts),
            "reasoning": "".join(reasoning_parts),
            "usage": usage,
            "model": self.config.model,
        }

    def close(self):
        if self._client is not None:
            self._client.close()
            self._client = None
