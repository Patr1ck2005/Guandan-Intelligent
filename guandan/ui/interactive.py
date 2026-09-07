"""Interactive game driver for human vs AI play.

Wraps the game engine to support step-driven (request/response) play
instead of the continuous game loop. Each step processes one human
move and auto-plays AI turns until it's the human's turn again.
"""

from __future__ import annotations

import random
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Dict, List, Optional, Tuple

from .. import constants
from ..card import Card
from ..combo import Combo
from ..combo_finder import ComboFinder
from ..combo_compare import can_beat
from ..deck import Deck
from ..game_state import GameState
from ..rules import RulesEngine, PlayLegality
from ..score import calculate_result, advance_level, is_game_won
from ..logging import game_logger
from ..state_utils import apply_play, start_new_trick, next_active_player
from ..table import TableState
from ..tribute import plan_tribute, execute_tribute

_PLAYER_NAMES = ["你", "AI-右", "AI-对", "AI-左"]


_DECIDER_ICONS = {
    "blind": "B",
    "mc": "MC",
    "ismcts": "TS",
    "llm": "AI",
}


class InteractiveGame:
    """Manages one Guandan game session with step-by-step human interaction.

    Human is always player 0. AI players are 1, 2, 3.
    """

    HUMAN_ID = 0

    def __init__(self, level: int = 2, agent_timeout_ms: float = 75000):
        self.level = level
        self.agent_timeout_ms = agent_timeout_ms
        self.team_levels = [level, level]
        self.round_number = 0
        self.state: Optional[GameState] = None
        self.rules: Optional[RulesEngine] = None
        self._round_results: list = []
        self._game_over = False
        self._winning_team: Optional[int] = None
        self._message = ""
        self._ai_running = False
        # Accumulated trick history across tricks within a round
        self._acc_trick_history: list = []
        # Tribute (进贡/还贡/抗贡) state
        self._tribute_records: list = []       # per-round exchange logs
        self._pending_return = None            # TributeExchange awaiting human 还贡
        self._tribute_banner = ""              # one-round banner text
        # AI config
        from ..ai.registry import DEFAULT_CONFIG
        import copy
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self._auto_play = False
        self._auto_human_pending = False
        self._game_started = False
        self._human_win_rate = None
        # Idempotency guard: the round_number whose result was already settled.
        # Re-arming _ai_running (e.g. by toggling auto-play while the round-over
        # panel is up) must never settle the same round twice — that used to
        # double-advance team levels and duplicate the scoreboard entry.
        self._settled_round: Optional[int] = None
        # Session lock: Flask dev server is threaded; overlapping /api/state
        # polls plus button POSTs used to race the state machine (double AI
        # steps, double settlement). One lock serializes every entry point.
        self._lock = threading.RLock()
        # Action log — memory module
        from ..ai.action_log import ActionLog
        self.action_log = ActionLog()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_new_round(self) -> dict:
        with self._lock:
            return self._start_new_round_locked()

    def _start_new_round_locked(self) -> dict:
        """Deal a new round and return the initial state for the frontend."""
        if self._game_over:
            return self._build_state()
        return self._start_round()

    def set_auto_play(self, enabled: bool) -> dict:
        """Toggle auto-play with guard rails.

        Returns the state payload; on refusal it carries error=True plus a
        message. Auto-play is refused while a round is over (the 下一局
        button owns the transition) and before the game started — arming
        the AI loop in those states used to re-run settlement.

        A pending tribute return no longer deadlocks the toggle: in
        auto-play the human seat is AI-driven anyway, so the return card is
        chosen automatically (lowest regular card) before arming.
        """
        with self._lock:
            if enabled:
                err = None
                if self._game_over:
                    err = "游戏已结束"
                elif self.state is not None and len(self.state.finished_positions) >= 3:
                    err = "本局已结束，请先点「下一局」"
                elif not self._game_started:
                    err = "请先点击「开始游戏」"
                if err:
                    state = self._build_state(error=err)
                    state["auto_play"] = False
                    return state
                if self._pending_return is not None:
                    self._auto_resolve_tribute_return()
            self._auto_play = bool(enabled)
            if self._auto_play and self.state is not None and not self._game_over \
                    and self._pending_return is None:
                self._ai_running = True
            state = self._build_state()
            state["auto_play"] = self._auto_play  # None-state payload lacks it
            return state

    def _auto_resolve_tribute_return(self):
        """Complete the human's pending tribute return with an AI-chosen
        card (lowest regular card; wilds/jokers kept for last). Only used
        when auto-play takes over the human seat."""
        ex = self._pending_return
        hand = self.state.hands[self.HUMAN_ID]
        regular = sorted(
            (c for c in hand if not c.is_wild(self.state.level) and not c.is_joker),
            key=lambda c: (c.rank.value, c.suit.value))
        card = regular[0] if regular else sorted(hand, key=lambda c: c.rank.value)[0]
        self._submit_tribute_return_locked([card.id])

    def play_cards(self, card_ids: List[int]) -> dict:
        with self._lock:
            return self._play_cards_locked(card_ids)

    def _play_cards_locked(self, card_ids: List[int]) -> dict:
        """Human plays the given cards."""
        if self._game_over:
            return self._build_state()
        if self._pending_return is not None:
            return self._build_state(error="请先完成还贡")
        if not self._game_started:
            return self._build_state(error="请先点击「开始游戏」")
        if self._ai_running:
            return self._build_state(error="AI思考中，请等待")
        if not self._is_human_turn():
            return self._build_state(error="现在不是你出牌")

        hand = self.state.hands[self.HUMAN_ID]
        cards = [c for c in hand if c.id in card_ids]

        if not cards:
            return self._build_state(error="未选中有效的牌")

        return self._process_human_play(cards)

    def pass_turn(self) -> dict:
        with self._lock:
            return self._pass_turn_locked()

    def _pass_turn_locked(self) -> dict:
        """Human passes. Returns updated state. Async AI processing."""
        if self._game_over:
            return self._build_state()
        if self._pending_return is not None:
            return self._build_state(error="请先完成还贡")
        if not self._game_started:
            return self._build_state(error="请先点击「开始游戏」")
        if self._ai_running:
            return self._build_state(error="AI思考中，请等待")
        if not self._is_human_turn():
            return self._build_state(error="现在不是你出牌")
        if not self.rules.can_pass(self.state.table, self.HUMAN_ID):
            return self._build_state(error="你是首家，不能过")

        return self._process_human_pass()

    def start_game(self) -> dict:
        with self._lock:
            return self._start_game_locked()

    def _start_game_locked(self) -> dict:
        """Called when human clicks '开始游戏'. Triggers AI if needed."""
        if self._game_started:
            return self._build_state()
        self._game_started = True
        if self.state is not None and self._pending_return is None:
            starter = self.state.current_player
            if starter != self.HUMAN_ID or self._auto_play:
                self._ai_running = True
        return self._build_state()

    def submit_tribute_return(self, card_ids: List[int]) -> dict:
        with self._lock:
            return self._submit_tribute_return_locked(card_ids)

    def _submit_tribute_return_locked(self, card_ids: List[int]) -> dict:
        """Human (as tribute receiver) returns the selected card."""
        if self._pending_return is None:
            return self._build_state(error="当前没有待还贡的牌")
        ex = self._pending_return
        if ex.receiver != self.HUMAN_ID:
            self._pending_return = None
            return self._build_state(error="你不是收贡方")
        hand = self.state.hands[self.HUMAN_ID]
        cards = [c for c in hand if c.id in card_ids]
        if len(cards) != 1:
            return self._build_state(error="请选择恰好一张牌还贡")
        card = cards[0]
        # Apply the deferred exchange: receiver loses `card`, payer regains
        # it. The tribute card already landed in the human's hand.
        hands_list = [tuple(h) for h in self.state.hands]
        hands_list[self.HUMAN_ID] = tuple(
            c for c in hands_list[self.HUMAN_ID] if c.id != card.id)
        hands_list[ex.payer] = hands_list[ex.payer] + (card,)
        self.state.hands = tuple(hands_list)
        ex.returned_card = card
        self._pending_return = None
        self._tribute_banner = (
            f"AI 进贡 {ex.paid_card.display}，你还贡 {card.display}")
        # Refresh the round's tribute record with the resolved return card.
        if self._tribute_records and \
                self._tribute_records[-1]["round"] == self.round_number:
            self._tribute_records[-1]["exchanges"] = [
                e.to_dict() for e in self._round_tribute_plan.exchanges]
        return self._start_round_after_tribute()

    def get_state(self) -> dict:
        """Return current game state. If AI is running, process one AI turn."""
        with self._lock:
            if self._ai_running and self.state is not None:
                self._step_one_ai()
            return self._build_state()

    def _is_human_turn(self) -> bool:
        """Check if it's currently the human's turn."""
        if self.state is None:
            return False
        if self._pending_return is not None:
            return False  # must resolve the tribute return first
        if len(self.state.finished_positions) >= 3:
            return False
        if self._ai_running:
            return False
        next_player = self._next_active_player(self.state.current_player)
        return next_player == self.HUMAN_ID

    # ------------------------------------------------------------------
    # Round management
    # ------------------------------------------------------------------

    def _start_round(self) -> dict:
        self.round_number += 1
        self._acc_trick_history = []  # reset accumulated history for new round
        # Reset per-round interaction state: a stale tribute banner or a
        # pending return from a previous round must never leak into a
        # fresh deal.
        self._pending_return = None
        self._tribute_banner = ""

        deck = Deck.shuffle(Deck.create())
        hands = Deck.deal(deck)

        tribute_plan = None
        first_round = self.round_number == 1 or not self._round_results
        if first_round:
            starter = random.randint(0, 3)
            round_level = self.team_levels[0]
        else:
            prev = self._round_results[-1]
            leader_team = constants.TEAMS[prev.positions[0]]
            # 级牌 = 头游队伍的级别
            round_level = self.team_levels[leader_team]
            tribute_plan = plan_tribute(prev.positions, self.team_levels,
                                        round_level, hands=hands)
            if tribute_plan.anti_tribute:
                starter = tribute_plan.leader  # 头游先出
                self._tribute_banner = "进贡方持双王，抗贡！头游先出"
                self._tribute_records.append({
                    "round": self.round_number, "anti_tribute": True,
                    "leader": starter, "exchanges": []})
            else:
                # Defer the human's return only in interactive mode; with
                # auto-play the human slot is AI-driven end-to-end.
                human_is_receiver = (not self._auto_play) and any(
                    ex.receiver == self.HUMAN_ID for ex in tribute_plan.exchanges)
                hands, tribute_plan = execute_tribute(
                    hands, tribute_plan, round_level,
                    defer_return_for=self.HUMAN_ID if human_is_receiver else None)
                starter = tribute_plan.leader
                human_ex = None
                for ex in tribute_plan.exchanges:
                    if ex.payer == self.HUMAN_ID:
                        self._tribute_banner = (
                            f"你进贡 {ex.paid_card.display}"
                            f"（{_PLAYER_NAMES[ex.receiver]} 收贡）")
                    elif ex.receiver == self.HUMAN_ID:
                        human_ex = ex
                if human_ex is not None:
                    self._pending_return = human_ex
                    self._tribute_banner = (
                        f"AI 进贡 {human_ex.paid_card.display}，请选一张牌还贡")
                elif len(tribute_plan.exchanges) > 1:
                    self._tribute_banner = " · ".join(
                        f"{_PLAYER_NAMES[e.payer]} 进贡 {e.paid_card.display}"
                        for e in tribute_plan.exchanges)
                self._tribute_records.append({
                    "round": self.round_number, "anti_tribute": False,
                    "leader": starter,
                    "exchanges": [e.to_dict() for e in tribute_plan.exchanges]})
            self._round_tribute_plan = tribute_plan

        self.rules = RulesEngine(round_level)
        from ..ai.action_log import ActionLog
        self.action_log = ActionLog()
        self.state = GameState(
            level=round_level,
            round_number=self.round_number,
            hands=hands,
            current_player=starter,
            trick_number=0,
        )

        # Don't auto-start AI — wait for "开始游戏" button
        self._game_started = False
        self._ai_running = False
        self._auto_human_pending = False
        self._message = "点击「开始游戏」" if not self._game_started else ""
        return self._build_state()

    def _start_round_after_tribute(self) -> dict:
        """After the human's 还贡, hands already reflect the exchange.
        Clear pending state and resume the round: arm the AI loop when the
        starter is an AI seat or auto-play is on (the game may have been
        started while the return was still pending)."""
        self._pending_return = None
        self._message = ""
        if self._game_started and self.state is not None:
            starter = self.state.current_player
            if starter != self.HUMAN_ID or self._auto_play:
                self._ai_running = True
        return self._build_state()

    # ------------------------------------------------------------------
    # Step processing
    # ------------------------------------------------------------------

    def _process_human_play(self, cards: List[Card]) -> dict:
        """Process human's play. AI turns will run step-by-step via get_state()."""
        result = self.rules.validate_play(
            cards=cards,
            hand=self.state.hands[self.HUMAN_ID],
            table_state=self.state.table,
            player_id=self.HUMAN_ID,
            finished_positions=self.state.finished_positions,
        )

        if not result.is_legal:
            return self._build_state(error=f"不合法的出牌: {result.reason.name}")

        self._apply_play(self.HUMAN_ID, cards, result.resolved_combo)
        self._compute_human_win_rate(cards)

        if self._check_round_end():
            return self._finish_round()

        self._ai_running = True
        return self._build_state(message="AI思考中...")

    def _process_human_pass(self) -> dict:
        """Process human pass. AI turns will run step-by-step via get_state()."""
        self.state.table.record_pass(self.HUMAN_ID)
        self.action_log.record_pass(self.state.trick_number, self.HUMAN_ID, self.state.table.current_combo)
        self.state.current_player = (self.HUMAN_ID + 1) % 4
        self._compute_human_win_rate([])  # empty = pass

        if self._check_round_end():
            return self._finish_round()

        self._ai_running = True
        return self._build_state(message="AI思考中...")

    def _step_one_ai(self):
        """Process exactly one AI turn. Called from get_state() while _ai_running."""
        if not self._ai_running:
            return
        if self.state is None:
            self._ai_running = False
            return
        if len(self.state.finished_positions) >= 3:
            self._finish_round()
            self._ai_running = False
            return
        if self._pending_return is not None:
            # Tribute exchange unresolved: never let the AI act (the human
            # slot is awaiting the return, and AI seats must not move past
            # an un-exchanged hand).
            self._ai_running = False
            return

        # Process deferred human auto-play (thinking glow was shown last poll)
        if self._auto_human_pending:
            self._auto_human_pending = False
            self._ai_play(self.HUMAN_ID)
            if len(self.state.finished_positions) >= 3:
                self._finish_round()
                self._ai_running = False
            return

        table = self.state.table

        # Check if trick ended
        if table.last_played_player >= 0:
            other_active = [p for p in self.state.active_players
                            if p != table.last_played_player]
            if table.pass_count >= len(other_active):
                self.state.current_player = table.last_played_player
                self._start_new_trick()
                if self.state.current_player == self.HUMAN_ID:
                    if self._auto_play:
                        pass  # continue to AI processing below
                    else:
                        self._ai_running = False
                        return

        # Get next player to act
        current = self._next_active_player(self.state.current_player)

        if current == self.HUMAN_ID:
            if self._auto_play:
                # Human slot is AI-controlled.
                # Defer to next poll so frontend can show thinking glow first.
                self.state.current_player = self.HUMAN_ID
                self._auto_human_pending = True
                return
            else:
                self.state.current_player = current
                self._ai_running = False
                return

        # Process this AI's turn
        self._ai_play(current)

        if len(self.state.finished_positions) >= 3:
            self._finish_round()
            self._ai_running = False
            return

        # Check if trick just ended (all others passed after this play)
        table = self.state.table
        if table.last_played_player >= 0:
            other_active = [p for p in self.state.active_players
                            if p != table.last_played_player]
            if table.pass_count >= len(other_active):
                self.state.current_player = table.last_played_player
                self._start_new_trick()
                if self.state.current_player == self.HUMAN_ID:
                    if self._auto_play:
                        pass
                    else:
                        self._ai_running = False
                        return

        # After all checks: if next active player is human (non-auto),
        # stop AI running to prevent a one-frame flash on the human zone.
        next_up = self._next_active_player(self.state.current_player)
        if next_up == self.HUMAN_ID and not self._auto_play:
            self._ai_running = False

    # ------------------------------------------------------------------
    # Auto-play AI turns
    # ------------------------------------------------------------------

    def _ai_play(self, player_id: int):
        """Have an AI player make a move (with restricted view).

        Never raises and never leaves the game stalled: an agent that
        crashes (e.g. poisoned numeric params) or an illegal play is
        downgraded to a forced legal play — passing as trick leader is
        forbidden by the rules and used to freeze the round forever.
        """
        from ..ai.player_view import PlayerView
        from ..ai.registry import create_agent_for_player
        hand = self.state.hands[player_id]

        play_cards = None
        try:
            agent = create_agent_for_player(self.config, player_id)
            view = PlayerView(self.state, player_id, self.action_log)

            if self.agent_timeout_ms > 0:
                timeout_s = self.agent_timeout_ms / 1000
                try:
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(agent.choose_play, view)
                        play_cards = future.result(timeout=timeout_s)
                except FutureTimeoutError:
                    game_logger.warning("agent_timeout", player=player_id,
                                        timeout_ms=self.agent_timeout_ms)
                    play_cards = None
            else:
                play_cards = agent.choose_play(view)
        except Exception as e:
            game_logger.warning("agent_error", player=player_id, error=str(e)[:200])
            play_cards = None

        if play_cards is None:
            play_cards = self._force_play(player_id, hand)

        result = self.rules.validate_play(
            cards=play_cards,
            hand=hand,
            table_state=self.state.table,
            player_id=player_id,
            finished_positions=self.state.finished_positions,
        )

        if not result.is_legal:
            is_leader = (self.state.table.is_empty
                         or self.state.table.last_played_player == player_id)
            if is_leader:
                # A leader cannot pass — force the smallest legal play so the
                # trick (and the round) can proceed.
                forced = self._force_play(player_id, hand)
                result = self.rules.validate_play(
                    cards=forced,
                    hand=hand,
                    table_state=self.state.table,
                    player_id=player_id,
                    finished_positions=self.state.finished_positions,
                )
                if not result.is_legal:
                    # Truly pathological hand — pass and accept the stall risk;
                    # _force_play covering a normal hand makes this unreachable.
                    self.state.table.record_pass(player_id)
                    self.action_log.record_pass(self.state.trick_number, player_id, self.state.table.current_combo)
                    self.state.current_player = (player_id + 1) % 4
                    return
                play_cards = forced
            else:
                self.state.table.record_pass(player_id)
                self.action_log.record_pass(self.state.trick_number, player_id, self.state.table.current_combo)
                self.state.current_player = (player_id + 1) % 4
                return

        if result.resolved_combo is not None and play_cards:
            self._apply_play(player_id, play_cards, result.resolved_combo)
        else:
            self.state.table.record_pass(player_id)
            self.action_log.record_pass(self.state.trick_number, player_id, self.state.table.current_combo)
            self.state.current_player = (player_id + 1) % 4

    def _apply_play(self, player_id: int, cards: List[Card], combo: Combo):
        """Apply a play: record in action log, then delegate to state_utils."""
        self.action_log.record_play(self.state.trick_number, player_id, cards, combo)
        apply_play(self.state, player_id, combo)

    def _start_new_trick(self):
        """Start a new trick: save history, then delegate to state_utils."""
        self._acc_trick_history.extend(self.state.table.trick_history)
        start_new_trick(self.state)

    def _next_active_player(self, from_player: int) -> int:
        return next_active_player(self.state, from_player)

    def _force_play(self, _player_id: int, hand: tuple) -> list:
        """Fallback when AI times out: smallest natural single, or pass."""
        from ..card import Rank
        from ..combo_parser import ComboParser
        normal = sorted(
            [c for c in hand if c.rank < Rank.SMALL_JOKER],
            key=lambda c: c.rank.value,
        )
        if normal:
            parser = ComboParser(self.state.level)
            combo = parser.parse([normal[0]])
            if combo:
                return [normal[0]]
        if self.state.table.is_empty:
            for c in hand:
                return [c]
        return []

    def _mask_state_for_player(self, player_id: int) -> GameState:
        """Return a copy of the state with opponent hands masked (empty tuples).

        AI agents must not see other players' specific cards — only hand sizes.
        """
        masked_hands = []
        for p in range(4):
            if p == player_id:
                masked_hands.append(self.state.hands[p])
            else:
                # Replace with empty tuple — AI only knows hand size via other means
                masked_hands.append(tuple())
        # Create a shallow copy with masked hands
        from ..game_state import GameState as GS
        from ..table import TableState
        return GS(
            level=self.state.level,
            round_number=self.state.round_number,
            hands=tuple(masked_hands),
            played_cards=list(self.state.played_cards),
            finished_positions=list(self.state.finished_positions),
            current_player=self.state.current_player,
            table=self.state.table,
            trick_number=self.state.trick_number,
        )

    def _compute_human_win_rate(self, cards: list):
        """MC to score the human's last action, using global config params."""
        try:
            from ..ai.player_view import PlayerView
            from ..ai.agent import MonteCarloAgent
            from ..combo_parser import ComboParser
            from ..ai.registry import create_agent_for_player
            agent = create_agent_for_player(self.config, self.HUMAN_ID)
            if not isinstance(agent, MonteCarloAgent):
                agent = MonteCarloAgent()
            view = PlayerView(self.state, self.HUMAN_ID, self.action_log)
            if not cards:
                scored = agent.score_candidates(view, [], can_pass=True)
                self._human_win_rate = scored[0][1] if scored else None
            else:
                parser = ComboParser(self.state.level)
                combo = parser.parse(cards)
                if combo:
                    scored = agent.score_candidates(view, [combo], can_pass=False)
                    self._human_win_rate = scored[0][1] if scored else None
        except Exception:
            self._human_win_rate = None

    def _check_round_end(self) -> bool:
        return self.state is not None and len(self.state.finished_positions) >= 3

    def _finish_round(self) -> dict:
        """Calculate round result and check for game over.

        Idempotent per round: re-entry after settlement (e.g. _ai_running
        re-armed while the round-over panel is up) returns the already
        recorded state instead of scoring the round again — the old
        behavior double-advanced team levels and duplicated scoreboard
        entries.
        """
        if self._settled_round == self.round_number and self._round_results:
            self._ai_running = False
            return self._build_state()
        self._settled_round = self.round_number
        self._auto_human_pending = False
        self._ai_running = False
        result = calculate_result(self.state.finished_positions)
        self._round_results.append(result)

        winning_team = result.winning_team

        if is_game_won(self.team_levels[winning_team]):
            self._game_over = True
            self._winning_team = winning_team
            self._message = f"游戏结束！{'你' if winning_team == 0 else 'AI'} 的队伍获胜！"
            return self._build_state()

        self.team_levels[winning_team] = advance_level(
            self.team_levels[winning_team], result.level_change
        )

        self._message = f"第{self.round_number}局结束，{'你' if winning_team == 0 else 'AI'}方升级"
        return self._build_state()

    # ------------------------------------------------------------------
    # State serialization
    # ------------------------------------------------------------------

    def _get_player_decider_info(self, player_id: int) -> dict:
        """Get human-readable decider info for a player from config."""
        from ..ai.registry import get_schema
        schema = get_schema()
        decider_cat = schema.get("decider", {}).get("models", {})

        player_cfg = self.config.get("players", {}).get(player_id)
        if player_cfg is None:
            player_cfg = self.config.get("players", {}).get(str(player_id), {})
        decider_id = player_cfg.get("decider")
        if not decider_id:
            decider_id = self.config.get("decider", {}).get("default_model", "mc")

        decider_def = decider_cat.get(decider_id, {})
        label = decider_def.get("name", decider_id)
        icon = _DECIDER_ICONS.get(decider_id, "?")
        description = decider_def.get("description", "")

        # Extract key params for display
        params = player_cfg.get("params") or {}
        defaults = self.config.get("decider", {}).get("model_params", {}).get(decider_id, {})
        merged = {**defaults, **params}

        param_summary = []
        param_defs = decider_def.get("params", {})
        for k, v in merged.items():
            pdef = param_defs.get(k, {})
            plabel = pdef.get("label", k)
            param_summary.append(f"{plabel}={v}")

        return {
            "id": decider_id,
            "label": label,
            "icon": icon,
            "description": description,
            "params": param_summary[:6],  # limit to 6 key params
        }

    def _build_state(self, error: str = "", message: str = "") -> dict:
        if self.state is None:
            return {
                "my_hand": [],
                "table_combo": None,
                "players": [],
                "my_turn": False,
                "can_pass": False,
                "level": self.level,
                "round": 0,
                "trick": 0,
                "finished_positions": [],
                "message": error or message or "开始新游戏",
                "error": bool(error),
                "round_over": False,
                "game_over": self._game_over,
                "winner": self._winning_team,
                "team_level_0": self.team_levels[0],
                "team_level_1": self.team_levels[1],
                "round_results": [],
                "trick_history": [],
                "tribute": {
                    "banner": "",
                    "pending_return": False,
                    "pending_return_card": None,
                    "records": [],
                },
                "game_started": self._game_started,
                "human_win_rate": None,
            }

        # Human hand (sorted by rank then suit)
        hand = sorted(
            self.state.hands[self.HUMAN_ID],
            key=lambda c: (c.rank.value, c.suit.value)
        )

        # Players info
        player_names = ["你", "AI-右", "AI-对", "AI-左"]
        players = []
        for p in range(4):
            is_finished = p in self.state.finished_positions
            players.append({
                "id": p,
                "name": player_names[p],
                "hand_size": 0 if is_finished else len(self.state.hands[p]),
                "finished": is_finished,
                "is_human": p == self.HUMAN_ID,
                "decider": self._get_player_decider_info(p),
            })

        # Table combo
        table_combo = None
        if self.state.table.current_combo is not None:
            tc = self.state.table.current_combo
            table_combo = {
                "type": tc.combo_type.name,
                "type_cn": _COMBO_TYPE_CN.get(tc.combo_type, tc.combo_type.name),
                "cards": [self._card_json(c) for c in tc.cards],
                "main_rank": tc.main_rank.name if tc.main_rank else "",
                "length": tc.length,
                "last_player": self.state.table.last_played_player,
            }

        # Check if human can pass
        can_pass = self.rules.can_pass(self.state.table, self.HUMAN_ID) if self.rules else False

        # Check if it's human's turn (AI must not be running)
        my_turn = not self._ai_running and self._is_human_turn()

        # Finished positions (player IDs in order)
        finished_positions = [
            {"id": pid, "name": player_names[pid]}
            for pid in self.state.finished_positions
        ]

        # Determine message
        if error:
            msg = error
        elif self._pending_return is not None:
            msg = "请选一张牌还贡"
        elif message:
            msg = message
        elif self._ai_running:
            msg = "AI思考中..."
        elif self._message:
            msg = self._message
            self._message = ""
        elif my_turn:
            if can_pass:
                msg = "请出牌或点「过」"
            else:
                msg = "你是首家，请出牌"
        else:
            msg = "等待AI出牌..."

        # Trick history — accumulated across tricks within a round
        trick_history = []
        all_entries = self._acc_trick_history + list(self.state.table.trick_history)
        for pid, combo in all_entries:
            if combo is None:
                trick_history.append({"player": pid, "pass": True})
            else:
                trick_history.append({
                    "player": pid,
                    "pass": False,
                    "combo": {
                        "type": combo.combo_type.name,
                        "type_cn": _COMBO_TYPE_CN.get(combo.combo_type, combo.combo_type.name),
                        "cards": [self._card_json(c) for c in combo.cards],
                        "length": combo.length,
                    }
                })

        # Round results history
        round_results = []
        for rr in self._round_results:
            round_results.append({
                "positions": [{"id": pid, "name": player_names[pid]} for pid in rr.positions],
                "winning_team": rr.winning_team,
                "level_change": rr.level_change,
            })

        # Tribute info (进贡/还贡/抗贡)
        pending_return_card = None
        if self._pending_return is not None and self._pending_return.paid_card:
            pending_return_card = self._card_json(self._pending_return.paid_card)
        tribute_block = {
            "banner": self._tribute_banner,
            "pending_return": self._pending_return is not None,
            "pending_return_card": pending_return_card,
            "records": self._tribute_records[-3:],  # last few rounds
        }

        return {
            "my_hand": [self._card_json(c) for c in hand],
            "table_combo": table_combo,
            "players": players,
            "my_turn": my_turn,
            "can_pass": can_pass,
            "level": self.state.level,
            "round": self.round_number,
            "trick": self.state.trick_number,
            "finished_positions": finished_positions,
            "message": msg,
            "error": bool(error),
            "round_over": len(self.state.finished_positions) >= 3,
            "game_over": self._game_over,
            "winner": self._winning_team,
            "team_level_0": self.team_levels[0],
            "team_level_1": self.team_levels[1],
            "round_results": round_results,
            "trick_history": trick_history,
            "tribute": tribute_block,
            "ai_running": self._ai_running,
            "ai_thinking_player": (
                self.HUMAN_ID if self._auto_human_pending
                else self.state.current_player if self._ai_running
                    and not (self.state.current_player == self.HUMAN_ID and not self._auto_play)
                else -1
            ),
            "auto_play": self._auto_play,
            "game_started": self._game_started,
            "human_win_rate": self._human_win_rate,
        }

    def _card_json(self, card: Card) -> dict:
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
            "is_wild": card.is_wild(self.state.level if self.state else 2),
            "is_joker": card.is_joker,
        }


_COMBO_TYPE_CN = {
    1: "单张", 2: "对子", 3: "三条", 4: "三带二",
    5: "顺子", 6: "连对", 7: "钢板", 8: "炸弹", 9: "同花顺", 10: "天王炸",
}
