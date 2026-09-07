"""Shared arena/eval helpers — model construction, scenario views, serialization.

Extracted from ``ui/web/arena_api.py`` so the Arena and Eval Lab share a single
implementation of model-building, scenario-view construction, and card
serialization (no duplicated semantics).
"""

from __future__ import annotations

from ..card import Card
from ..combo_parser import ComboParser
from ..game_state import GameState
from ..table import TableState
from ..ai.player_view import PlayerView
from ..ai.registry import create_agent_for_player


def _card_json(card: Card, level: int = 2) -> dict:
    suit_names = {0: "C", 1: "D", 2: "H", 3: "S", 4: ""}
    rank_names = {2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8",
                  9: "9", 10: "10", 11: "J", 12: "Q", 13: "K", 14: "A",
                  15: "SJ", 16: "BJ"}
    return {
        "id": card.id,
        "rank": card.rank.value,
        "rank_name": rank_names.get(card.rank.value, "?"),
        "suit": card.suit.value,
        "suit_name": suit_names.get(card.suit.value, ""),
        "display": card.display,
        "is_wild": card.is_wild(level),
        "is_joker": card.is_joker,
    }


def _cards_json(card_ids: list, level: int = 2) -> list:
    return [_card_json(Card.from_id(i), level) for i in card_ids]


def _rank_signature(card_ids) -> tuple:
    """Rank-multiset of card IDs, used for intent-based correctness comparison.

    Scenarios' ``expected_play`` picks fresh cards of the same ranks as the hand
    (not the hand's actual IDs), so exact-ID equality is wrong — compare ranks.
    """
    return tuple(sorted(Card.from_id(i).rank.value for i in card_ids))


def _build_view(scenario, perspective: int = 0):
    """Build a PlayerView for the given scenario and player perspective.

    All cards not assigned to any player's hand are treated as
    already played, keeping opponent hands small and coherent.
    """
    table_ids = set(scenario.table or [])

    # Collect all explicitly assigned card IDs
    assigned = set(scenario.hand)
    for ids in scenario.opponents.values():
        assigned.update(ids)
    assigned.update(scenario.played_cards)
    assigned.update(table_ids)

    # Build hands — undefined = 0 cards (finished)
    raw_hands = {0: tuple(Card.from_id(i) for i in scenario.hand)}
    for p, ids in scenario.opponents.items():
        raw_hands[int(p)] = tuple(Card.from_id(i) for i in ids)
    for p in range(4):
        if p not in raw_hands:
            raw_hands[p] = tuple()
    hands_tuple = tuple(raw_hands[p] for p in range(4))

    # Every card not in a hand → "already played"
    all_unused = [i for i in range(108) if i not in assigned]
    played_ids = list(scenario.played_cards) + all_unused
    played_cards = [Card.from_id(i) for i in played_ids]

    table = TableState(trick_leader=scenario.table_player)
    if scenario.table:
        parser = ComboParser(scenario.level)
        combo = parser.parse([Card.from_id(i) for i in scenario.table])
        if combo:
            table.record_play(scenario.table_player, combo)

    state = GameState(level=scenario.level, round_number=1,
                      hands=hands_tuple, current_player=perspective,
                      table=table, trick_number=1,
                      played_cards=played_cards)
    return PlayerView(state, perspective)


def _make_arena_agent(profile: dict):
    """Build an agent that implements .analyze(view) → AnalyzeResult.

    MC deciders (mc, ismcts) go through registry.create_agent_for_player()
    for real parameter passing. Non-MC deciders use TestableModel wrappers.

    Unknown model ids return (None, label) so callers can report a per-model
    error — the old else-branch silently ran BlindScorer under the unknown
    id's name (a typo'd id looked like a successful blind result).
    """
    model_id = profile.get("id", "blind")
    label = profile.get("label", model_id)

    # LLM — build LLMAgent directly (it implements .analyze())
    if model_id == "llm":
        from ..ai.llm import LLMAgent, LLMConfig
        llm_fields = {f.name for f in LLMConfig.__dataclass_fields__.values()}
        llm_params = {k: v for k, v in profile.items() if k in llm_fields}
        return LLMAgent(config=LLMConfig(**llm_params)), label or "LLM"

    # Non-MC deciders — use TestableModel directly, pass all profile params
    if model_id not in ("mc", "ismcts"):
        # Extract model constructor params (filter out metadata keys)
        model_params = {k: v for k, v in profile.items()
                        if k not in ("id", "label")}
        if model_id == "informed":
            from ..ai.models import InformedScorer
            return InformedScorer(**model_params), label
        elif model_id == "round":
            from ..ai.models import RoundScorer
            return RoundScorer(**model_params), label
        elif model_id == "exact":
            from ..ai.models import EndgameExactSolver
            return EndgameExactSolver(**model_params), label
        elif model_id == "blind":
            from ..ai.models import BlindScorer
            return BlindScorer(**model_params), label
        else:
            return None, label

    # MC deciders — build config from profile and use registry factory
    decider_params = {}
    for key in ("num_samples", "time_limit_ms", "max_iterations", "ucb_c",
                "sampler", "inner", "enumerator", "top_n", "pre_scorer"):
        if key in profile:
            decider_params[key] = profile[key]

    # Extract nested inner/enumerator params from dotted keys like "inner.round_weight"
    inner_id = decider_params.get("inner", "informed")
    enum_id = decider_params.get("enumerator", "full")
    inner_params = {}
    enum_params = {}
    for k, v in profile.items():
        if k.startswith("inner."):
            inner_params[k[6:]] = v
        elif k.startswith("enumerator."):
            enum_params[k[11:]] = v

    config = {
        "players": {0: {"decider": model_id, "params": decider_params}},
        "decider": {
            "default_model": model_id,
            "model_params": {model_id: decider_params},
        },
        "inner_model": {
            "model_params": {
                "blind": {}, "informed": {}, "round": {}, "exact": {},
                inner_id: inner_params,
            },
        },
        "enumerator": {
            "model_params": {
                "full": {}, "top_n": {}, "memory": {},
                enum_id: enum_params,
            },
        },
    }

    try:
        agent = create_agent_for_player(config, 0)
        return agent, label
    except Exception:
        return None, label
