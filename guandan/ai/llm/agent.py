"""LLMAgent — end-to-end LLM-driven game decision making."""

from __future__ import annotations

import time
from typing import List, Tuple

from ...card import Card
from ...combo import Combo
from ...combo_finder import ComboFinder
from ...combo_parser import ComboParser
from ...rules import RulesEngine
from ...logging import ai_log
from ..player_view import PlayerView
from ..models.interface import AnalyzeResult, CandidateResult
from ..agent import (
    BaseAgent,
    HeuristicAgent,
    MonteCarloAgent,
    _generate_lead_candidates,
    _enumerate_responses,
)
from ..params import DEFAULT_PARAMS
from .config import LLMConfig, DEFAULT_LLM_CONFIG
from .client import (
    DeepSeekAPIClient,
    LLMClientError,
    LLMAuthError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from .prompts import SYSTEM_PROMPT, build_user_message
from .parser import ResponseParser, ResponseParseError
from .tracker import TokenTracker, get_token_tracker
from .cache import get_response_cache


class LLMAgent(BaseAgent):
    """AI agent that delegates card-play decisions to a large language model.

    Two modes:
    - candidate (default): pre-computes legal combos, LLM chooses the best index.
      Safe, reliable, lower token cost.
    - free: LLM selects raw card IDs from its hand. More creative but needs
      strict validation.

    Implements both `choose_play(view)` (BaseAgent interface, used by the game
    engine) and `analyze(view)` (Arena testable-model interface, returns an
    AnalyzeResult with candidates/choice/metrics).
    """

    name = "LLMAgent"
    description = "LLM-driven: DeepSeek API end-to-end decisions"

    def __init__(self, config: LLMConfig | None = None, **kwargs):
        if config is not None:
            self.llm_config = config
        else:
            merged = {**DEFAULT_LLM_CONFIG.__dict__}
            # Filter out non-LLMConfig params that might slip through
            valid_keys = {f.name for f in LLMConfig.__dataclass_fields__.values()}
            filtered = {k: v for k, v in {**merged, **kwargs}.items() if k in valid_keys}
            self.llm_config = LLMConfig(**filtered)

        self._client = DeepSeekAPIClient(self.llm_config)
        self._fallback = self._create_fallback()
        self._tracker = get_token_tracker() if self.llm_config.track_tokens else None
        self._cache = get_response_cache() if self.llm_config.use_cache else None
        self._stats = {"success": 0, "fallback": 0, "total": 0}

    # ------------------------------------------------------------------
    # BaseAgent interface
    # ------------------------------------------------------------------

    def choose_play(self, view: PlayerView) -> List[Card]:
        """Main entry point for the game engine (via ThreadPoolExecutor)."""
        result = self.analyze(view)
        if result.choice and result.choice.card_ids:
            card_ids = set(result.choice.card_ids)
            return [c for c in view.my_hand if c.id in card_ids]
        return []

    # ------------------------------------------------------------------
    # Arena testable-model interface
    # ------------------------------------------------------------------

    def analyze(self, view: PlayerView) -> AnalyzeResult:
        """Run one LLM decision and return a full AnalyzeResult."""
        self._stats["total"] += 1
        pid = view.player_id
        hand = view.my_hand
        table = view.table
        level = view.level
        t0 = time.time()

        can_pass = not table.is_empty and table.last_played_player != pid

        # Candidate mode: precompute legal combos (also surfaced as candidates)
        candidates = None
        if self.llm_config.mode == "candidate":
            candidates = self._precompute_candidates(view)
            candidates = self._order_candidates(candidates)
            if not candidates:
                # No legal play — pass if allowed, else nothing. Skip the API call.
                return self._build_result(
                    [], candidates, can_pass, t0, fallback=False, response=None, level=level
                )

        # Build user message
        opponent_sizes = {p: view.opponent_hand_size(p) for p in range(4) if p != pid}
        user_msg = build_user_message(
            hand=hand,
            level=level,
            player_id=pid,
            table_combo=table.current_combo,
            table_last_player=table.last_played_player,
            trick_history=table.trick_history,
            opponent_sizes=opponent_sizes,
            finished=view.finished_positions,
            round_num=view.round_number,
            trick_num=view.trick_number,
            candidates=candidates,
            can_pass=can_pass,
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]

        # Resolve response — cache hit avoids re-spending tokens
        cache_key = None
        response = None
        if self._cache is not None:
            cache_key = self._cache.make_key(
                self.llm_config.model,
                self.llm_config.mode,
                self.llm_config.temperature,
                self.llm_config.max_tokens,
                user_msg,
            )
            response = self._cache.get(cache_key)
        if response is None:
            try:
                response = self._client.chat(messages, self._tracker)
            except Exception as e:
                return self._handle_fallback(view, e, t0)
            if self._cache is not None and cache_key is not None:
                self._cache.set(cache_key, response)

        # Log reasoning if available (deepseek-reasoner)
        reasoning = response.get("reasoning", "")
        if reasoning:
            ai_log(pid, "llm_reasoning", reasoning=reasoning[:2000])

        # Parse response — try content first, fallback to reasoning
        raw = response["content"]
        if not raw.strip() and reasoning:
            # Some reasoner models put the final answer in reasoning_content
            raw = reasoning

        try:
            if self.llm_config.mode == "candidate":
                parser = ResponseParser(level, hand)
                cards = parser.parse_candidate_mode(raw, candidates, can_pass)
            else:
                parser = ResponseParser(level, hand)
                cards = parser.parse_free_mode(raw)
        except ResponseParseError as e:
            return self._handle_fallback(view, e, t0)

        # Resolve None → empty list (pass)
        if cards is None:
            cards = []

        # Validate via RulesEngine
        if cards:
            try:
                self._validate_against_rules(cards, view)
            except ResponseParseError as e:
                return self._handle_fallback(view, e, t0)

        elapsed = (time.time() - t0) * 1000
        self._stats["success"] += 1
        ai_log(
            pid,
            "llm_decision_end",
            mode=self.llm_config.mode,
            model=self.llm_config.model,
            choice=("pass" if not cards else f"{len(cards)} cards"),
            cards=[c.display for c in cards],
            elapsed_ms=round(elapsed, 1),
            tokens=response.get("usage", {}).get("total_tokens", 0),
            success_count=self._stats["success"],
            fallback_count=self._stats["fallback"],
        )
        return self._build_result(cards, candidates, can_pass, t0, fallback=False, response=response, level=level)

    # ------------------------------------------------------------------
    # Result construction
    # ------------------------------------------------------------------

    def _build_result(
        self,
        cards: List[Card],
        candidates: List[Combo] | None,
        can_pass: bool,
        t0: float,
        fallback: bool,
        response: dict | None,
        level: int,
    ) -> AnalyzeResult:
        elapsed = (time.time() - t0) * 1000
        crs = self._candidate_results(candidates, can_pass)
        choice = self._choice_result(cards, level, fallback)
        metrics = {
            "elapsed_ms": round(elapsed, 1),
            "model": self.llm_config.model,
            "mode": self.llm_config.mode,
            "tokens": (response or {}).get("usage", {}).get("total_tokens", 0),
            "fallback": fallback,
            "success_count": self._stats["success"],
            "fallback_count": self._stats["fallback"],
        }
        return AnalyzeResult(
            candidates=crs,
            choice=choice,
            pass_chosen=(len(cards) == 0),
            metrics=metrics,
            model_name="LLMAgent",
        )

    @staticmethod
    def _candidate_results(candidates: List[Combo] | None, can_pass: bool) -> List[CandidateResult]:
        """Serialize candidate combos for the Arena UI (scores unknown, so None)."""
        crs = []
        if candidates:
            for combo in candidates:
                crs.append(CandidateResult(
                    combo_type=combo.combo_type.name,
                    cards=[c.display for c in combo.cards],
                    card_ids=[c.id for c in combo.cards],
                ))
        if can_pass:
            crs.append(CandidateResult(combo_type="PASS", cards=[], card_ids=[]))
        return crs

    @staticmethod
    def _choice_result(cards: List[Card], level: int, fallback: bool) -> CandidateResult:
        if not cards:
            return CandidateResult(combo_type="PASS", cards=[], card_ids=[])
        combo_type = "FALLBACK" if fallback else "PLAY"
        try:
            combo = ComboParser(level).parse(tuple(cards))
            if combo:
                combo_type = combo.combo_type.name
        except Exception:
            pass
        return CandidateResult(
            combo_type=combo_type,
            cards=[c.display for c in cards],
            card_ids=[c.id for c in cards],
        )

    # ------------------------------------------------------------------
    # Candidate generation
    # ------------------------------------------------------------------

    def _precompute_candidates(self, view: PlayerView) -> List[Combo]:
        """Pre-compute legal combos using existing fast enumeration."""
        hand = view.my_hand
        table = view.table
        pid = view.player_id
        level = view.level
        finder = ComboFinder(hand, level)

        if table.is_empty or table.last_played_player == pid:
            candidates = _generate_lead_candidates(finder, hand)
        else:
            candidates = _enumerate_responses(hand, table.current_combo, finder, level)

        if len(candidates) > self.llm_config.max_candidates:
            candidates = self._trim_candidates(candidates, hand, table, level)
        return candidates

    def _order_candidates(self, candidates: List[Combo]) -> List[Combo]:
        """Order candidates based on config. Default 'natural' = no reordering."""
        order = self.llm_config.candidate_order
        if order == "by_length_desc":
            return sorted(candidates, key=lambda c: -c.length)
        # "natural" — keep original order from candidate generation
        return candidates

    def _trim_candidates(
        self,
        candidates: List[Combo],
        hand: Tuple[Card, ...],
        table,
        level: int,
    ) -> List[Combo]:
        """Pre-score and keep top N candidates when there are too many."""
        from ..scorer import score_play

        scored = []
        for c in candidates:
            used_ids = {x.id for x in c.cards}
            hand_after = tuple(x for x in hand if x.id not in used_ids)
            s = score_play(c, hand, hand_after, table.current_combo, level, DEFAULT_PARAMS)
            scored.append((c, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [c for c, _ in scored[: self.llm_config.max_candidates]]

    def _validate_against_rules(self, cards: List[Card], view: PlayerView):
        """Validate the LLM's chosen cards against the RulesEngine."""
        rules = RulesEngine(view.level)
        result = rules.validate_play(
            cards=cards,
            hand=view.my_hand,
            table_state=view.table,
            player_id=view.player_id,
            finished_positions=view.finished_positions,
        )
        if not result.is_legal:
            raise ResponseParseError(
                f"LLM play rejected by rules engine: {result.reason}"
            )

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------

    def _create_fallback(self) -> BaseAgent:
        fb = self.llm_config.fallback_agent
        if fb == "mc":
            return MonteCarloAgent(num_samples=24, time_limit_ms=3000)
        if fb == "pass":
            return _PassAgent()
        return HeuristicAgent(params=DEFAULT_PARAMS)

    def _handle_fallback(
        self, view: PlayerView, error: Exception, t0: float
    ) -> AnalyzeResult:
        self._stats["fallback"] += 1
        elapsed = (time.time() - t0) * 1000
        ai_log(
            view.player_id,
            "llm_fallback",
            error_type=type(error).__name__,
            error_msg=str(error)[:300],
            elapsed_ms=round(elapsed, 1),
            success_count=self._stats["success"],
            fallback_count=self._stats["fallback"],
        )
        cards = self._fallback.choose_play(view)
        can_pass = not view.table.is_empty and view.table.last_played_player != view.player_id
        crs = self._candidate_results(None, can_pass)
        choice = self._choice_result(cards, view.level, fallback=True)
        return AnalyzeResult(
            candidates=crs,
            choice=choice,
            pass_chosen=(len(cards) == 0),
            metrics={
                "elapsed_ms": round(elapsed, 1),
                "fallback": True,
                "error": str(error)[:200],
                "success_count": self._stats["success"],
                "fallback_count": self._stats["fallback"],
            },
            model_name="LLMAgent",
        )


class _PassAgent(BaseAgent):
    """Always passes — used as a minimal fallback."""

    def choose_play(self, view: PlayerView) -> List[Card]:
        if view.table.is_empty:
            # Must play something — play smallest single
            hand = view.my_hand
            if hand:
                return [min(hand, key=lambda c: c.rank.value)]
            return []
        return []  # pass
