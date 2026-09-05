"""Tests for the LLM agent module."""

import json
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from guandan.card import Card, Rank, Suit
from guandan.combo import Combo, ComboType
from guandan.table import TableState
from guandan.game_state import GameState
from guandan.ai.player_view import PlayerView
from guandan.ai.llm.config import LLMConfig, load_api_key
from guandan.ai.llm.tracker import TokenTracker, TokenUsage, get_token_tracker
from guandan.ai.llm.parser import ResponseParser, ResponseParseError
from guandan.ai.llm.prompts import (
    SYSTEM_PROMPT,
    build_user_message,
    COMBO_TYPE_LABEL,
)
from guandan.ai.llm.agent import LLMAgent
from guandan.ai.llm.client import LLMClientError
from guandan.ai.llm.cache import LLMResponseCache
from guandan.combo_parser import ComboParser


# ==================================================================
# Helpers
# ==================================================================

def make_card(card_id: int) -> Card:
    return Card.from_id(card_id)


def make_hand(card_ids: list) -> tuple:
    return tuple(Card.from_id(i) for i in card_ids)


# ==================================================================
# Config tests
# ==================================================================

class TestLLMConfig:
    def test_defaults(self):
        cfg = LLMConfig()
        assert cfg.model == "deepseek-reasoner"
        assert cfg.mode == "candidate"
        assert cfg.max_tokens == 1024
        assert cfg.max_retries == 2
        assert cfg.fallback_agent == "heuristic"

    def test_override(self):
        cfg = LLMConfig(model="deepseek-reasoner", temperature=0.0, mode="free")
        assert cfg.model == "deepseek-reasoner"
        assert cfg.temperature == 0.0
        assert cfg.mode == "free"

    def test_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-key")
        cfg = LLMConfig()
        assert cfg.api_key == "sk-test-key"

    def test_load_api_key(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        # .env file may contain key; just verify non-empty when env is set
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-abc")
        assert load_api_key() == "sk-abc"


# ==================================================================
# TokenTracker tests
# ==================================================================

class TestTokenTracker:
    def test_record_and_summary(self):
        tt = TokenTracker()
        tt.record({"prompt_tokens": 500, "completion_tokens": 20, "total_tokens": 520}, "deepseek-chat")
        assert tt.call_count == 1
        assert tt.game_total.total_tokens == 520
        assert tt.game_total.cost_usd > 0

    def test_reset_game(self):
        tt = TokenTracker()
        tt.record({"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}, "deepseek-chat")
        tt.reset_game()
        assert tt.call_count == 0
        assert tt.game_total.total_tokens == 0
        # All-time should persist
        assert tt.all_time_total.total_tokens == 110

    def test_global_singleton(self):
        t1 = get_token_tracker()
        t2 = get_token_tracker()
        assert t1 is t2


# ==================================================================
# Parser tests
# ==================================================================

class TestResponseParser:
    def test_candidate_mode_choose_first(self):
        hand = make_hand([0, 13, 26, 39])  # Four cards, all rank 2
        cards = list(hand[:2])             # 0=rank2/suit0, 13=rank2/suit1 → valid PAIR
        combo = Combo(
            combo_type=ComboType.PAIR,
            cards=tuple(cards),
            main_rank=Rank.TWO,
            length=2,
        )
        parser = ResponseParser(level=2, hand=hand)
        # When can_pass=False, choices are 0-indexed
        result = parser.parse_candidate_mode(
            '{"choice": 0}', [combo], can_pass=False
        )
        assert result == cards

    def test_candidate_mode_pass(self):
        hand = make_hand([0, 1, 2, 3, 4, 5])
        parser = ResponseParser(level=2, hand=hand)
        result = parser.parse_candidate_mode(
            '{"choice": 0}', [], can_pass=True
        )
        assert result is None

    def test_candidate_mode_out_of_range(self):
        hand = make_hand([0, 1, 2, 3, 4, 5])
        parser = ResponseParser(level=2, hand=hand)
        with pytest.raises(ResponseParseError, match="out of range"):
            parser.parse_candidate_mode('{"choice": 99}', [], can_pass=False)

    def test_free_mode_play(self):
        # Card 0 = rank2/suit0, Card 13 = rank2/suit1 → valid PAIR
        hand = make_hand([0, 13, 26, 39])
        parser = ResponseParser(level=5, hand=hand)
        result = parser.parse_free_mode('{"play": [0, 13]}')
        assert result is not None
        assert len(result) == 2

    def test_free_mode_pass(self):
        hand = make_hand([0, 1, 2, 3])
        parser = ResponseParser(level=2, hand=hand)
        result = parser.parse_free_mode('{"play": "pass"}')
        assert result is None

    def test_free_mode_card_not_in_hand(self):
        hand = make_hand([0, 1, 2, 3])
        parser = ResponseParser(level=2, hand=hand)
        with pytest.raises(ResponseParseError, match="not in your hand"):
            parser.parse_free_mode('{"play": [99]}')

    def test_free_mode_invalid_combo(self):
        # 0=rank2, 1=rank3, 3=rank5, 5=rank7 — not consecutive, not same rank, not any valid combo
        hand = make_hand([0, 1, 3, 5, 7, 9])
        parser = ResponseParser(level=2, hand=hand)
        with pytest.raises(ResponseParseError, match="do not form a valid combo"):
            parser.parse_free_mode('{"play": [0, 1, 3, 5]}')

    def test_extract_json_from_text(self):
        hand = make_hand([0, 1])
        parser = ResponseParser(level=2, hand=hand)
        # JSON wrapped in extra text
        result = parser.parse_free_mode('Here is my choice: {"play": "pass"}')
        assert result is None

    def test_malformed_json(self):
        hand = make_hand([0, 1])
        parser = ResponseParser(level=2, hand=hand)
        with pytest.raises(ResponseParseError, match="Could not extract valid JSON"):
            parser.parse_free_mode("not json at all")


# ==================================================================
# Prompt tests
# ==================================================================

class TestPrompts:
    def test_system_prompt_not_empty(self):
        assert len(SYSTEM_PROMPT) > 500
        assert "Guandan" in SYSTEM_PROMPT
        assert "SINGLE" in SYSTEM_PROMPT
        assert "BOMB" in SYSTEM_PROMPT

    def test_build_user_message_candidate_mode(self):
        hand = make_hand([0, 1, 2, 3, 13, 14, 26, 27, 39, 40])
        table = TableState()
        cards = list(hand[:2])
        combo = Combo(
            combo_type=ComboType.PAIR,
            cards=tuple(cards),
            main_rank=Rank.THREE,
            length=2,
        )
        table.record_play(1, combo)

        candidates = [combo]
        msg = build_user_message(
            hand=hand,
            level=2,
            player_id=0,
            table_combo=table.current_combo,
            table_last_player=table.last_played_player,
            trick_history=table.trick_history,
            opponent_sizes={1: 25, 2: 26, 3: 26},
            finished=[],
            round_num=1,
            trick_num=1,
            candidates=candidates,
            can_pass=True,
        )
        assert "Your Hand" in msg
        assert "Hand Analysis" in msg
        assert "Table" in msg
        assert "Opponents" in msg
        assert "Your Legal Plays" in msg
        assert "0. PASS" in msg
        assert "1." in msg
        assert "PAIR" in msg

    def test_build_user_message_free_mode(self):
        hand = make_hand([0, 1, 2])
        msg = build_user_message(
            hand=hand,
            level=2,
            player_id=0,
            table_combo=None,
            table_last_player=-1,
            trick_history=[],
            opponent_sizes={1: 27, 2: 27, 3: 27},
            finished=[],
            round_num=1,
            trick_num=1,
            candidates=None,
            can_pass=False,
        )
        assert "Your Cards" in msg
        assert "play" in msg

    def test_hand_display_groups_by_rank(self):
        # 0=deck0/suit0/rank2, 1=deck0/suit1/rank2, 13=deck0/suit0/rank3
        hand = make_hand([0, 1, 13])
        msg = build_user_message(
            hand=hand,
            level=5,
            player_id=0,
            table_combo=None,
            table_last_player=-1,
            trick_history=[],
            opponent_sizes={1: 27, 2: 27, 3: 27},
            finished=[],
            round_num=1,
            trick_num=1,
            candidates=None,
            can_pass=False,
        )
        assert "TWO" in msg or "2" in msg


# ==================================================================
# analyze() / Arena integration tests
# ==================================================================

def make_view(hand_ids, table_combo=None, table_player=1, level=2):
    """Build a PlayerView for player 0 with a simple 4-hand state."""
    hand = make_hand(hand_ids)
    hands = (
        hand,
        make_hand([13, 14, 15]),
        make_hand([26, 27, 28]),
        make_hand([39, 40, 41]),
    )
    table = TableState(trick_leader=table_player)
    if table_combo is not None:
        table.record_play(table_player, table_combo)
    state = GameState(level=level, round_number=1, hands=hands,
                      current_player=0, table=table, trick_number=1)
    return PlayerView(state, 0)


def make_llm_agent(use_cache=False, **kwargs):
    cfg = LLMConfig(mode="candidate", use_cache=use_cache, **kwargs)
    return LLMAgent(config=cfg)


class TestLLMAnalyze:
    def test_candidate_mode_leader_choice(self, monkeypatch):
        agent = make_llm_agent()

        def fake_chat(messages, tracker=None):
            return {"content": '{"choice": 0}', "reasoning": "",
                    "usage": {"total_tokens": 10}, "model": "deepseek-chat"}

        monkeypatch.setattr(agent._client, "chat", fake_chat)

        # hand = ♣2 ♣3 ♣4 ♣5 ♣6 → first candidate is SINGLE ♣2 (id 0)
        view = make_view([0, 1, 2, 3, 4])
        result = agent.analyze(view)

        assert result.choice is not None
        assert result.pass_chosen is False
        assert result.choice.card_ids == [0]
        assert result.metrics["fallback"] is False

    def test_candidate_mode_pass(self, monkeypatch):
        agent = make_llm_agent()

        def fake_chat(messages, tracker=None):
            return {"content": '{"choice": 0}', "reasoning": "",
                    "usage": {}, "model": "deepseek-chat"}

        monkeypatch.setattr(agent._client, "chat", fake_chat)

        # P1 led SINGLE ♣3; P0 holds ♣4 ♣5 ♣6 and chooses to pass (choice 0)
        table_combo = ComboParser(2).parse([Card.from_id(1)])
        view = make_view([2, 3, 4], table_combo=table_combo, table_player=1)
        result = agent.analyze(view)

        assert result.pass_chosen is True
        assert result.choice.combo_type == "PASS"

    def test_no_candidates_short_circuits_without_api_call(self, monkeypatch):
        agent = make_llm_agent()
        called = {"n": 0}

        def fake_chat(messages, tracker=None):
            called["n"] += 1
            return {"content": "{}", "reasoning": "", "usage": {}, "model": "x"}

        monkeypatch.setattr(agent._client, "chat", fake_chat)

        # P1 led SINGLE ♣A (id 12); P0 holds ♣3 ♣4 ♣5 ♣6 ♣7 — every single
        # is below A, no bomb in hand. At level 2 all suit runs are legal SFs
        # that beat a single, so use off-suit mixed cards with no 5-run in
        # one suit and no 4-of-a-kind: ♣3 ♥4 ♦5 ♠6 ♣9.
        table_combo = ComboParser(2).parse([Card.from_id(12)])
        view = make_view([1, 17, 31, 45, 9], table_combo=table_combo,
                         table_player=1)
        result = agent.analyze(view)

        assert called["n"] == 0
        assert result.pass_chosen is True

    def test_fallback_on_client_error(self, monkeypatch):
        agent = make_llm_agent()

        def fake_chat(messages, tracker=None):
            raise LLMClientError("boom")

        monkeypatch.setattr(agent._client, "chat", fake_chat)

        view = make_view([0, 1, 2, 3, 4])
        result = agent.analyze(view)

        assert result.metrics["fallback"] is True
        assert result.choice is not None

    def test_cache_hit_skips_second_call(self, tmp_path, monkeypatch):
        agent = make_llm_agent(use_cache=True)
        agent._cache = LLMResponseCache(path=str(tmp_path / "cache.json"))
        calls = {"n": 0}

        def fake_chat(messages, tracker=None):
            calls["n"] += 1
            return {"content": '{"choice": 0}', "reasoning": "",
                    "usage": {"total_tokens": 10}, "model": "deepseek-chat"}

        monkeypatch.setattr(agent._client, "chat", fake_chat)

        view = make_view([0, 1, 2, 3, 4])
        r1 = agent.analyze(view)
        r2 = agent.analyze(view)

        assert calls["n"] == 1
        assert r1.choice.card_ids == r2.choice.card_ids


class TestArenaRouting:
    def test_make_arena_agent_routes_llm(self):
        from guandan.ui.web.arena_api import _make_arena_agent

        agent, label = _make_arena_agent({"id": "llm", "label": "LLM 大模型"})

        assert isinstance(agent, LLMAgent)
        assert hasattr(agent, "analyze")
        assert label == "LLM 大模型"
