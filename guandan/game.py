"""Game orchestrator: manages rounds, tricks, and player turns."""

from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import constants
from .card import Card
from .combo import Combo
from .deck import Deck
from .game_state import GameState
from .rules import PlayLegality, RulesEngine, ValidationResult
from .score import RoundResult, advance_level, calculate_result, is_game_won
from .logging import game_logger
from .state_utils import next_active_player
from .table import TableState
from .tribute import plan_tribute, execute_tribute


@dataclass
class GameResult:
    """Final result of a complete game."""
    winning_team: int
    final_levels: Tuple[int, int]
    rounds_played: int
    round_results: List[RoundResult] = field(default_factory=list)
    tribute_records: List[dict] = field(default_factory=list)


class Game:
    """Orchestrates a full Guandan game."""

    MAX_TRICKS_PER_ROUND = 500
    MAX_TRICK_ITERATIONS = 200

    def __init__(self, agents: List, level: int = 2, agent_timeout_ms: float = 75000,
                 on_move=None):
        if len(agents) != 4:
            raise ValueError(f"Need exactly 4 agents, got {len(agents)}")
        self.agents = agents
        self.agent_timeout_ms = agent_timeout_ms
        self.on_move = on_move  # optional callback after each play/pass (for progress)
        self.rules = RulesEngine(level)
        self.state: Optional[GameState] = None
        self.team_levels = [level, level]  # [team_0, team_1]
        self.round_results: List[RoundResult] = []
        self.tribute_records: List[dict] = []  # per-round tribute exchange logs
        self.round_number = 0
        # Public action memory for opponent modeling (stage 4.2); reset each
        # round since the deck is re-dealt.
        from .ai.action_log import ActionLog
        self.action_log = ActionLog()

    def play_game(self) -> GameResult:
        """Play until one team wins.

        Win condition: win a round while your team's level is at A (14) or higher.
        """
        game_logger.game("game_start", level=self.team_levels[0])
        try:
            hint = None
            while True:
                round_result = self._play_round(starter_hint=hint)
                self.round_results.append(round_result)
                hint = round_result.positions[0]  # 头游 leads the next round

                winning_team = round_result.winning_team

                # Win: already at level A (or above) and won the round
                if is_game_won(self.team_levels[winning_team]):
                    game_logger.game("game_end", winner=winning_team,
                                    levels=list(self.team_levels),
                                    rounds=self.round_number)
                    return GameResult(
                        winning_team=winning_team,
                        final_levels=(self.team_levels[0], self.team_levels[1]),
                        rounds_played=self.round_number,
                        round_results=self.round_results,
                        tribute_records=self.tribute_records,
                    )

                self.team_levels[winning_team] = advance_level(
                    self.team_levels[winning_team], round_result.level_change
                )
        except Exception:
            game_logger.warning("game_error", error=str(Exception))
            raise

    def _play_round(self, starter_hint: Optional[int] = None) -> RoundResult:
        """Play one round (one hand).

        级牌归属: the round's level is the 头游 team's level (previous round's
        winner leads both the level and the first trick). Tribute is planned
        and executed after dealing, before the first trick.
        """
        self.round_number += 1
        # 级牌 = 头游队伍的级别 (round 1: both teams share the base level)
        if starter_hint is not None and self.round_number > 1:
            level = self.team_levels[constants.TEAMS[starter_hint]]
        else:
            level = self.team_levels[0]
        self.rules = RulesEngine(level)

        # Deal
        deck = Deck.shuffle(Deck.create())
        hands = Deck.deal(deck)

        # Determine starter & tribute.
        tribute_plan = None
        if self.round_number == 1 or starter_hint is None:
            starter = random.randint(0, 3)
        else:
            # Tribute for the upcoming round: the previous round's finish
            # order decides payers/receivers; 抗贡 needs the dealt hands.
            prev = self.round_results[-1]
            next_level = self.team_levels[constants.TEAMS[prev.positions[0]]]
            tribute_plan = plan_tribute(prev.positions, self.team_levels,
                                        next_level, hands=hands)
            if tribute_plan.anti_tribute:
                starter = tribute_plan.leader  # 抗贡 → 头游先出
            else:
                hands, tribute_plan = execute_tribute(hands, tribute_plan, level)
                starter = tribute_plan.leader
            self.tribute_records.append(tribute_plan.to_dict())
            if tribute_plan.exchanges:
                game_logger.game(
                    "tribute", round=self.round_number,
                    exchanges=[
                        {"payer": e.payer, "receiver": e.receiver,
                         "paid": e.paid_card.display if e.paid_card else None,
                         "returned": e.returned_card.display if e.returned_card else None}
                        for e in tribute_plan.exchanges],
                    anti=tribute_plan.anti_tribute, leader=tribute_plan.leader)
            elif tribute_plan.anti_tribute:
                game_logger.game("tribute_anti", round=self.round_number,
                                 leader=tribute_plan.leader)

        self.state = GameState(
            level=level,
            round_number=self.round_number,
            hands=hands,
            current_player=starter,
            trick_number=0,
        )
        self.action_log = type(self.action_log)()  # fresh memory per dealt hand

        game_logger.game("round_start", round=self.round_number,
                         level=level, starter=starter,
                         tribute="anti" if (tribute_plan and tribute_plan.anti_tribute)
                         else ("yes" if (tribute_plan and tribute_plan.exchanges) else "no"))

        # Play tricks until 3 players empty their hands
        round_iters = 0
        while len(self.state.finished_positions) < 3:
            self._play_trick()
            round_iters += 1
            if round_iters > self.MAX_TRICKS_PER_ROUND:
                hands_left = {p: len(self.state.hands[p]) for p in self.state.active_players}
                raise IllegalPlayError(
                    f"Round exceeded 500 tricks! finished={self.state.finished_positions}, "
                    f"hands={hands_left}",
                    player_id=-1, reason=PlayLegality.INVALID_COMBO,
                )

        result = calculate_result(self.state.finished_positions)
        game_logger.game("round_end", round=self.round_number,
                         winner=result.winning_team,
                         level_change=result.level_change,
                         positions=result.positions)
        return result

    def _play_trick(self):
        """Play one trick (一轮)."""
        self.state.trick_number += 1
        leader = next_active_player(self.state, self.state.current_player)
        self.state.table.reset_for_new_trick(leader)
        current = leader
        moved_combo = None  # combo played this iteration (None = pass), for on_move
        game_logger.game_debug("trick_start", trick=self.state.trick_number,
                               leader=leader)
        _iter = 0

        while _iter < self.MAX_TRICK_ITERATIONS:
            _iter += 1
            current = next_active_player(self.state,current)

            # Check if trick should end: all other active players have passed
            if self.state.table.last_played_player >= 0:
                other_active = len([
                    p for p in self.state.active_players
                    if p != self.state.table.last_played_player
                ])
                if self.state.table.pass_count >= other_active:
                    break

            # Get agent's play (with timeout)
            agent = self.agents[current]
            hand = self.state.hands[current]
            from .ai.player_view import PlayerView
            view = PlayerView(self.state, current,
                              action_log=self.action_log,
                              tribute_records=self.tribute_records)

            if self.agent_timeout_ms > 0:
                timeout_s = self.agent_timeout_ms / 1000
                executor = ThreadPoolExecutor(max_workers=1)
                try:
                    future = executor.submit(agent.choose_play, view)
                    play_cards = future.result(timeout=timeout_s)
                except FutureTimeoutError:
                    game_logger.warning("agent_timeout", player=current,
                                        timeout_ms=self.agent_timeout_ms)
                    play_cards = self._force_play(current, hand)
                    # Do NOT block on shutdown: the worker keeps running until the
                    # agent itself returns, so the timeout truly caps this move.
                    executor.shutdown(wait=False, cancel_futures=True)
                else:
                    executor.shutdown(wait=True)
            else:
                play_cards = agent.choose_play(view)

            # Validate
            result = self.rules.validate_play(
                cards=play_cards,
                hand=hand,
                table_state=self.state.table,
                player_id=current,
                finished_positions=self.state.finished_positions,
            )

            if not result.is_legal:
                game_logger.warning("illegal_play", player=current,
                                    reason=getattr(result.reason, 'name', str(result.reason)))
                # Coerce to a safe legal move rather than aborting the game.
                play_cards = self._force_play(current, hand)
                result = self.rules.validate_play(
                    cards=play_cards, hand=hand, table_state=self.state.table,
                    player_id=current, finished_positions=self.state.finished_positions)
                if not result.is_legal:
                    play_cards = []
                    result = None

            if play_cards:
                # Play: update table and hand
                combo = result.resolved_combo
                moved_combo = combo
                self.state.table.record_play(current, combo)
                self.action_log.record_play(self.state.trick_number, current,
                                            play_cards, combo)
                # Remove cards from hand
                played_ids = {c.id for c in play_cards}
                new_hand = tuple(c for c in hand if c.id not in played_ids)
                self.state.hands = tuple(
                    new_hand if i == current else h
                    for i, h in enumerate(self.state.hands)
                )
                self.state.played_cards.extend(play_cards)

                game_logger.game_debug(
                    "play", player=current, combo=combo.combo_type.name,
                    cards=len(play_cards), hand_remaining=len(new_hand),
                    trick=self.state.trick_number,
                )

                # Check finish
                if not new_hand:
                    self.state.finished_positions.append(current)
                    game_logger.game("player_finished", player=current,
                                     position=len(self.state.finished_positions))
                    # If 3 players finished, trick ends
                    if len(self.state.finished_positions) >= 3:
                        break
            else:
                # Pass
                moved_combo = None
                self.state.table.record_pass(current)
                self.action_log.record_pass(self.state.trick_number, current,
                                            self.state.table.current_combo)
                game_logger.game_debug("pass", player=current,
                                       trick=self.state.trick_number)

            if self.on_move:
                self.on_move(self, current, moved_combo)

            # Next player (clockwise, skip finished)
            current = (current + 1) % 4
        else:
            hands_info = [(p, len(self.state.hands[p])) for p in self.state.active_players]
            raise IllegalPlayError(
                f"Trick loop exceeded {self.MAX_TRICK_ITERATIONS} iters. Active: {hands_info}, "
                f"finished: {self.state.finished_positions}, pass: {self.state.table.pass_count}, "
                f"last_played: {self.state.table.last_played_player}",
                player_id=-1,
                reason=PlayLegality.INVALID_COMBO,
            )

        # Trick winner leads next
        if self.state.table.last_played_player >= 0:
            self.state.current_player = self.state.table.last_played_player

    def _force_play(self, player_id: int, hand: tuple) -> list:
        """Fallback when an agent times out or plays illegally.

        Leader: play the smallest single. Follower: pass (always legal).
        """
        from .card import Rank
        is_leader = self.state.table.is_empty or self.state.table.last_played_player == player_id
        if is_leader:
            normal = sorted(
                [c for c in hand if c.rank < Rank.SMALL_JOKER],
                key=lambda c: c.rank.value,
            )
            if normal:
                return [normal[0]]
            for c in hand:
                return [c]
            return []
        return []  # follower passes


class IllegalPlayError(Exception):
    """Raised when an agent makes an illegal play."""

    def __init__(self, message: str, player_id: int, reason: PlayLegality):
        super().__init__(message)
        self.player_id = player_id
        self.reason = reason
