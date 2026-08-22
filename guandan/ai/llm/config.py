"""LLM configuration dataclass with environment variable loading."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class LLMConfig:
    """Centralized configuration for the LLM agent.

    api_key is always loaded from the DEEPSEEK_API_KEY environment variable
    and is NEVER serialized to config.json.
    """

    api_base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    model: str = "deepseek-reasoner"
    mode: str = "candidate"
    max_candidates: int = 200
    candidate_order: str = "natural"
    temperature: float = 0.6
    max_tokens: int = 1024    # response only; reasoning is separate
    stream: bool = True
    timeout_s: float = 45.0    # reasoner needs time but must be < game engine 75s
    max_retries: int = 2
    fallback_agent: str = "heuristic"
    track_tokens: bool = True
    use_cache: bool = True

    def __post_init__(self):
        if not self.api_key:
            self.api_key = load_api_key()


def load_api_key() -> str:
    """Load the DeepSeek API key from environment or .env file."""
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        # Try loading from .env file (fallback for non-Flask usage)
        try:
            from dotenv import load_dotenv as _load
            _load()
            key = os.environ.get("DEEPSEEK_API_KEY", "")
        except ImportError:
            pass
    return key


DEFAULT_LLM_CONFIG = LLMConfig()
