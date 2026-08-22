"""Response parsing and validation for LLM outputs."""

from __future__ import annotations

import json
import re
from typing import List, Tuple

from ...card import Card
from ...combo import Combo
from ...combo_parser import ComboParser


class ResponseParseError(Exception):
    """Raised when the LLM response cannot be parsed into a valid action."""


class ResponseParser:
    """Parses LLM JSON responses into validated Card lists."""

    def __init__(self, level: int, hand: Tuple[Card, ...]):
        self._parser = ComboParser(level)
        self._level = level
        self._hand = hand
        self._hand_ids: set = {c.id for c in hand}

    def parse_candidate_mode(
        self,
        response_text: str,
        candidates: List[Combo],
        can_pass: bool,
    ) -> List[Card] | None:
        """Parse candidate-mode response. Returns cards or None (pass)."""
        data = self._extract_json(response_text)
        choice = self._get_int(data, "choice")
        if choice is None:
            raise ResponseParseError("Missing or invalid 'choice' in response")

        if can_pass and choice == 0:
            return None  # pass

        offset = 1 if can_pass else 0
        idx = choice - offset
        if idx < 0 or idx >= len(candidates):
            raise ResponseParseError(
                f"Choice {choice} out of range (valid: 0-{len(candidates) - offset})"
            )
        return list(candidates[idx].cards)

    def parse_free_mode(self, response_text: str) -> List[Card] | None:
        """Parse free-mode response. Returns cards or None (pass)."""
        data = self._extract_json(response_text)
        play = data.get("play")
        if play is None:
            raise ResponseParseError("Missing 'play' key in response")

        if play == "pass":
            return None

        if not isinstance(play, list):
            raise ResponseParseError(f"Expected 'play' to be 'pass' or list of ints, got {type(play)}")

        if len(play) == 0:
            raise ResponseParseError("Empty card list — use 'pass' to pass")

        # Resolve card IDs
        cards = []
        for cid in play:
            if not isinstance(cid, int):
                raise ResponseParseError(f"Card ID must be integer, got {cid}")
            if cid not in self._hand_ids:
                raise ResponseParseError(f"Card ID {cid} is not in your hand")
            cards.append(Card.from_id(cid))

        # Validate combo
        combo = self._parser.parse(tuple(cards))
        if combo is None:
            raise ResponseParseError(
                f"Cards {[c.display for c in cards]} do not form a valid combo"
            )
        return cards

    def _extract_json(self, text: str) -> dict:
        """Extract the first JSON object from response text."""
        text = text.strip()
        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find JSON object with regex
        m = re.search(r"\{[^{}]*\}", text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass

        # Try nested braces (the LLM may have put JSON in markdown)
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass

        raise ResponseParseError(f"Could not extract valid JSON from response: {text[:200]}")

    @staticmethod
    def _get_int(data: dict, key: str) -> int | None:
        val = data.get(key)
        if val is None:
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None
