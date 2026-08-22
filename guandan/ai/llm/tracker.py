"""Token usage and cost tracking for LLM calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
        )


class TokenTracker:
    """Tracks token usage and cost per game and across all games."""

    PRICING = {
        "deepseek-chat": {"input": 0.27, "output": 1.10},
        "deepseek-reasoner": {"input": 0.55, "output": 2.19},
    }

    def __init__(self):
        self._call_count: int = 0
        self._game_usage = TokenUsage()
        self._total_usage = TokenUsage()

    def record(self, usage: dict, model: str):
        prompt = usage.get("prompt_tokens", 0)
        completion = usage.get("completion_tokens", 0)
        total = usage.get("total_tokens", prompt + completion)

        price = self.PRICING.get(model, self.PRICING["deepseek-chat"])
        cost = (prompt / 1_000_000) * price["input"] + (
            completion / 1_000_000
        ) * price["output"]

        entry = TokenUsage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            cost_usd=cost,
        )
        self._game_usage += entry
        self._total_usage += entry
        self._call_count += 1

    @property
    def call_count(self) -> int:
        return self._call_count

    @property
    def game_total(self) -> TokenUsage:
        return self._game_usage

    @property
    def all_time_total(self) -> TokenUsage:
        return self._total_usage

    def reset_game(self):
        self._game_usage = TokenUsage()
        self._call_count = 0

    def summary(self) -> str:
        g = self._game_usage
        t = self._total_usage
        return (
            f"LLM tokens — Game: {g.total_tokens} tokens / ${g.cost_usd:.4f} in {self._call_count} calls | "
            f"All-time: {t.total_tokens} tokens / ${t.cost_usd:.4f}"
        )


_token_tracker: TokenTracker | None = None


def get_token_tracker() -> TokenTracker:
    global _token_tracker
    if _token_tracker is None:
        _token_tracker = TokenTracker()
    return _token_tracker
