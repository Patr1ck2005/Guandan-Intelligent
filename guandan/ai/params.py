"""Unified AI parameters — all tunable weights in one place.

These control the scoring and decision logic. Adjustable per-model
and per-player via the web UI config panel.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AIParams:
    """Tunable parameters for AI decision-making."""

    # Efficiency — how much to value using many cards at once
    efficiency_weight: float = 20.0

    # Round reduction — reward for reducing rounds-to-empty (scorer.py uses this)
    round_weight: float = 8.0

    # Bomb penalties
    bomb_lead_penalty: float = -12.0    # leading with a bomb
    bomb_overuse_penalty: float = -10.0  # using bomb vs non-bomb
    bomb_vs_bomb_bonus: float = 2.0     # bomb vs bomb

    # Positional
    lead_bonus: float = 1.0
    follow_bonus: float = 1.0
    joker_lead_penalty: float = -18.0      # per joker card led (eff>=16)
    rank_card_lead_penalty: float = -10.0   # per rank/level card led (eff==15)
    high_rank_lead_penalty: float = -0.5    # leading with K/A (eff>=13)

    # Card usage
    card_usage_weight: float = 0.3

    # Threshold: score below this → pass instead of play
    pass_threshold: float = 0.0

    # 2v2 teammate awareness (public info only; 0.0 = off = no behavior change).
    # These converge conceptually with the informed/round scorers' fixed
    # teammate_cover_bonus, but operate on PUBLIC information (seat parity,
    # trick history, finished positions) so BlindAgent stays blind and fast.
    support_teammate_lead: float = 0.0    # teammate owns the table → prefer low follow cards (送牌)
    block_opponent_lead: float = 0.0      # opponent owns table & teammate passed → bonus for covering (压牌)
    push_teammate_endgame: float = 0.0    # teammate finished → per-card bonus to finish fast (护头游)

    # Simulation agent: probability of passing when could play
    sim_pass_prob: float = 0.15

    # Logging
    log_level: str = "INFO"
    log_file_enabled: bool = True
    log_console_enabled: bool = True

    def to_dict(self) -> dict:
        return {f: getattr(self, f) for f in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, d: dict) -> AIParams:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# Default instance
DEFAULT_PARAMS = AIParams()
