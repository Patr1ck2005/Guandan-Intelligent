"""Play scoring with tunable parameters.

Core concept: "rounds to empty" (轮次).
  - Bombs cost 0 rounds (playable over anything, anytime).
  - All other combos cost 1 round each.
  - Goal: minimise rounds to empty your hand.
"""

from __future__ import annotations

from typing import Tuple

from ..card import Card, effective_rank
from ..combo import Combo
from .hand_eval import estimate_rounds
from .params import AIParams, DEFAULT_PARAMS


def teammate_context_score(
    candidate: Combo,
    table_combo: Combo | None,
    level: int,
    context: dict | None,
    params: AIParams = DEFAULT_PARAMS,
) -> float:
    """2v2 teammate-aware score adjustment, using PUBLIC information only
    (seat parity for the partner, trick history, finished positions).

    All weights default to 0.0 in AIParams → returns 0.0, so behaviour is
    byte-identical until a weight is explicitly enabled (and then quantified
    via the eval-history store).

    context keys (all optional):
      player_id: int            — the deciding player's seat
      last_played_player: int   — seat owning the table combo (-1 if lead)
      teammate_passed: bool     — partner passed within the current trick
      finished_positions: list  — seats already done this round
    """
    if not context:
        return 0.0
    p = params
    score = 0.0
    pid = context.get("player_id", 0)
    teammate = (pid + 2) % 4
    finished = context.get("finished_positions") or []
    last_player = context.get("last_played_player", -1)

    # 队友已头游 → push to finish (both lead and follow)
    if p.push_teammate_endgame and teammate in finished:
        factor = 0.5 if candidate.is_bomb else 1.0
        score += p.push_teammate_endgame * candidate.length * factor

    if table_combo is None:
        return score

    # 队友控场 → follow low, hand the trick to the partner (送牌)
    if p.support_teammate_lead and last_player == teammate and not candidate.is_bomb:
        eff = effective_rank(candidate.main_rank, level)
        score += p.support_teammate_lead * max(0.0, 16.0 - eff) / 16.0

    # 对手控场且队友已过 → cover the trick (压牌)
    if (p.block_opponent_lead and last_player >= 0 and last_player != teammate
            and context.get("teammate_passed") and not candidate.is_bomb):
        score += p.block_opponent_lead

    return score


def score_play(
    candidate: Combo,
    hand_before: Tuple[Card, ...],
    hand_after: Tuple[Card, ...],
    table_combo: Combo | None,
    level: int,
    params: AIParams = DEFAULT_PARAMS,
    context: dict | None = None,
) -> float:
    """Score a candidate play. Higher = better. Negative = pass is preferred.

    ``context`` carries public teammate/seat info for the 2v2 heuristics
    (see teammate_context_score); None keeps the classic behaviour.
    """
    rounds_before = estimate_rounds(hand_before, level)
    rounds_after = estimate_rounds(hand_after, level)
    round_delta = rounds_before - rounds_after

    round_score = round_delta * params.round_weight

    bomb_score = 0.0
    if candidate.is_bomb:
        if table_combo is not None and not table_combo.is_bomb:
            if round_delta >= 0:
                bomb_score = params.bomb_overuse_penalty
            else:
                bomb_score = 0.0
        elif table_combo is None:
            bomb_score = params.bomb_lead_penalty
        else:
            bomb_score = params.bomb_vs_bomb_bonus

    usage_score = candidate.length * params.card_usage_weight

    positional = 0.0
    if table_combo is None:
        positional += params.lead_bonus
        rv = effective_rank(candidate.main_rank, level)
        # Per-card penalty: count cards matching main_rank in this combo
        rank_card_count = sum(1 for c in candidate.cards if c.rank == candidate.main_rank)
        if rv >= 16:  # jokers
            positional += params.joker_lead_penalty * max(1, rank_card_count)
        elif rv == 15:  # level card (级牌)
            positional += params.rank_card_lead_penalty * max(1, rank_card_count)
        elif rv >= 13:  # K, A
            positional += params.high_rank_lead_penalty
    else:
        if not candidate.is_bomb:
            positional += params.follow_bonus

    return (round_score + bomb_score + usage_score + positional
            + teammate_context_score(candidate, table_combo, level, context, params))


def choose_best_play(
    candidates: list[Combo],
    hand: Tuple[Card, ...],
    table_combo: Combo | None,
    level: int,
    can_pass: bool = False,
    params: AIParams = DEFAULT_PARAMS,
    context: dict | None = None,
) -> Combo | None:
    """Pick the best play from candidates, or None if passing is better."""
    if not candidates:
        return None

    best = None
    best_score = float('-inf')

    for combo in candidates:
        used_ids = {c.id for c in combo.cards}
        hand_after = tuple(c for c in hand if c.id not in used_ids)
        s = score_play(combo, hand, hand_after, table_combo, level, params,
                       context=context)
        if s > best_score:
            best_score = s
            best = combo

    if can_pass and best_score < params.pass_threshold:
        return None
    return best
