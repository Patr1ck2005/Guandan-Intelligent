"""Rules engine: validates play legality."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import List, Optional

from .card import Card
from .combo import Combo, ComboType
from .combo_parser import ComboParser
from .combo_compare import can_beat
from .table import TableState


class PlayLegality(Enum):
    LEGAL = auto()
    CARDS_NOT_IN_HAND = auto()
    INVALID_COMBO = auto()
    WRONG_TYPE = auto()
    TOO_WEAK = auto()
    CANNOT_PASS_AS_LEADER = auto()
    ALREADY_FINISHED = auto()


@dataclass
class ValidationResult:
    is_legal: bool
    reason: PlayLegality
    resolved_combo: Optional[Combo] = None


class RulesEngine:
    """Validate plays according to Guandan rules."""

    def __init__(self, level: int):
        self.level = level
        self.parser = ComboParser(level)

    def validate_play(
        self,
        cards: List[Card],
        hand: Tuple[Card, ...],
        table_state: TableState,
        player_id: int,
        finished_positions: List[int],
    ) -> ValidationResult:
        """Validate whether `player_id` can legally play `cards` given the game state.

        Args:
            cards: The cards the player wants to play (empty list = pass).
            hand: The player's current hand.
            table_state: Current trick state.
            player_id: The player making the play.
            finished_positions: List of players who have already finished.

        Returns:
            ValidationResult with is_legal=True only if the play is valid.
        """
        # Player already finished
        if player_id in finished_positions:
            return ValidationResult(False, PlayLegality.ALREADY_FINISHED)

        # Pass: allowed only when NOT the trick leader
        if not cards:
            if table_state.is_empty or table_state.last_played_player == player_id:
                return ValidationResult(False, PlayLegality.CANNOT_PASS_AS_LEADER)
            return ValidationResult(True, PlayLegality.LEGAL)

        # Verify all played cards are in hand
        hand_ids = {c.id for c in hand}
        for c in cards:
            if c.id not in hand_ids:
                return ValidationResult(False, PlayLegality.CARDS_NOT_IN_HAND)

        # Parse the combo
        combo = self.parser.parse(cards)
        if combo is None:
            return ValidationResult(False, PlayLegality.INVALID_COMBO)

        # If table is empty, any valid combo is legal (player is leader)
        if table_state.is_empty:
            return ValidationResult(True, PlayLegality.LEGAL, combo)

        # Table has a combo — must beat it
        if not can_beat(combo, table_state.current_combo):
            # Determine why (a bomb vs non-bomb can't reach here: can_beat
            # would have accepted it, so no special bomb branch is needed).
            if not combo.is_bomb and table_state.current_combo.is_bomb:
                return ValidationResult(False, PlayLegality.TOO_WEAK, combo)
            if combo.combo_type != table_state.current_combo.combo_type:
                return ValidationResult(False, PlayLegality.WRONG_TYPE, combo)
            return ValidationResult(False, PlayLegality.TOO_WEAK, combo)

        return ValidationResult(True, PlayLegality.LEGAL, combo)

    def can_pass(self, table_state: TableState, player_id: int) -> bool:
        """A player can pass only when they are not the trick leader."""
        if table_state.is_empty:
            return False
        return table_state.last_played_player != player_id
