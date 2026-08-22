"""MC Decider — samples opponent hands, simulates with an inner decider, picks best."""

from __future__ import annotations

import time
from typing import List, Optional, Tuple

from ..card import Card
from ..combo import Combo
from ..combo_finder import ComboFinder
from ..combo_compare import can_beat
from ..game_state import GameState
from ..rules import RulesEngine
from ..table import TableState
from ..score import calculate_result
from .player_view import PlayerView
from ..state_utils import clone_state, apply_play, start_new_trick, next_active_player, did_team_win
from .sampler import Sampler, create_sampler
from ..logging import ai_log


class MCDecider:
    """Monte Carlo decision maker: sample + simulate + vote.

    Composes:
      sampler: generates opponent hands from unseen pool
      inner:   the decider used inside each simulation (must have choose_play(view))
    """

    name = "MCDecider"
    description = "MC决策器：采样 unseen → 内层决策器模拟 → 胜率投票"

    def __init__(self, sampler: Sampler = None, inner=None,
                 enumerator=None,
                 num_samples: int = 128, time_limit_ms: int = 10000,
                 config: dict = None):
        self.sampler = sampler or create_sampler("random")
        self.inner = inner
        self.enumerator = enumerator
        self.num_samples = num_samples
        self.time_limit_ms = time_limit_ms
        self._config = config

    def choose_play(self, view: PlayerView):
        """Main entry point. Returns list[Card] like BaseAgent."""
        result = self.analyze(view)
        if result.choice and result.choice.combo_type != "PASS":
            card_ids = set(result.choice.card_ids)
            return [c for c in view.my_hand if c.id in card_ids]
        return []

    def analyze(self, view: PlayerView):
        t_start = time.time()
        pid = view.player_id
        table = view.table
        level = view.level
        hand = view.my_hand
        finder = ComboFinder(hand, level)

        ai_log(pid, "decision_start", agent="MCDecider",
               hand_size=len(hand), samples=self.num_samples)

        # Generate candidates via pluggable enumerator
        is_lead = table.is_empty or table.last_played_player == pid
        table_combo = table.current_combo if not is_lead else None
        if self.enumerator:
            try:
                candidates = self.enumerator.enumerate(hand, view, is_lead, table_combo, level)
            except TypeError:
                # Older enumerator that doesn't accept 'view'
                candidates = self.enumerator.enumerate(hand, None, is_lead, table_combo, level)
        elif is_lead:
            from .agent import _generate_lead_candidates
            candidates = _generate_lead_candidates(finder, hand)
        else:
            from .agent import _enumerate_responses
            candidates = _enumerate_responses(hand, table_combo, finder, level)

        can_pass = table_combo is not None and table.last_played_player != pid

        all_candidates = list(candidates)
        if can_pass:
            all_candidates.append(None)  # pass

        results = []  # list of (combo, wr, sims, wins)
        timed_out = False
        deadline = t_start + self.time_limit_ms / 1000  # HARD cap on the decision

        for candidate in all_candidates:
            if time.time() > deadline:
                timed_out = True
                break

            if candidate is None:
                results.append((None, 0.0, 0, 0))
                continue

            wins = 0
            sims = 0
            for _ in range(self.num_samples):
                if time.time() > deadline:
                    timed_out = True
                    break
                if self._run_one_simulation(view, candidate, deadline):
                    wins += 1
                sims += 1

            wr = wins / sims if sims > 0 else 0.0
            results.append((candidate, wr, sims, wins))

        best = max(results, key=lambda r: r[1]) if results else (None, 0.0)
        best_wr = best[1]
        elapsed = (time.time() - t_start) * 1000

        # Build AnalyzeResult
        from .models.interface import AnalyzeResult, CandidateResult
        crs = []
        sim_agent_name = getattr(self.inner, 'name', 'Blind') if self.inner else 'Blind'
        for c, wr, sims, wins in results:
            detail = {"samples": sims, "wins": wins, "sim_agent": sim_agent_name}
            if c is None:
                crs.append(CandidateResult(
                    combo_type="PASS", cards=[], card_ids=[],
                    win_rate=wr, score=wr, detail=detail))
            else:
                crs.append(CandidateResult(
                    combo_type=c.combo_type.name,
                    cards=[x.display for x in c.cards],
                    card_ids=[x.id for x in c.cards],
                    win_rate=wr, score=wr, detail=detail))

        crs.sort(key=lambda r: r.win_rate or 0, reverse=True)
        best_cr = crs[0] if crs else None

        ai_log(pid, "decision_end", agent="MCDecider",
               candidates_scored=[{
                   "type": c.combo_type,
                   "cards": c.cards,
                   "win_rate": round(c.win_rate or 0, 3),
               } for c in crs],
               choice=(best[0].combo_type.name if best[0] else "PASS"),
               choice_cards=[x.display for x in best[0].cards] if best[0] else [],
               choice_win_rate=round(best_wr, 3),
               elapsed_ms=round(elapsed, 1),
               timed_out=timed_out)

        return AnalyzeResult(
            candidates=crs, choice=best_cr,
            pass_chosen=(best[0] is None),
            metrics={"elapsed_ms": elapsed, "timed_out": timed_out},
            model_name="MCDecider")

    def _run_one_simulation(self, view: PlayerView, my_play: Combo,
                            deadline: float | None = None) -> bool:
        """Run one simulation. Returns True if my team wins.

        ``deadline`` is a monotonic wall-clock cap (from the decision start);
        if exceeded mid-simulation the sim stops early and reports the current
        state, so ``time_limit_ms`` is a HARD bound on each MC decision.
        """
        pid = view.player_id
        my_team = {0: 0, 1: 1, 2: 0, 3: 1}[pid]

        # Clone state
        sim_state = clone_state(view._state)

        # Apply my play
        apply_play(sim_state, pid, my_play)

        if len(sim_state.finished_positions) >= 3:
            return did_team_win(sim_state, my_team)

        # Deal unseen cards randomly
        sampled = self.sampler.sample(view)
        for p in range(4):
            if p == pid:
                continue
            sim_state.hands = tuple(
                sampled.get(p, tuple()) if i == p else h
                for i, h in enumerate(sim_state.hands))

        # Simulate to end
        for _ in range(200):
            if deadline is not None and time.time() > deadline:
                return did_team_win(sim_state, my_team)  # hard stop; report partial state
            if len(sim_state.finished_positions) >= 3:
                return did_team_win(sim_state, my_team)

            table = sim_state.table
            if table.last_played_player >= 0:
                other = [p for p in sim_state.active_players
                         if p != table.last_played_player]
                if table.pass_count >= len(other):
                    sim_state.current_player = table.last_played_player
                    start_new_trick(sim_state)
                    continue

            current = next_active_player(sim_state, sim_state.current_player)
            hand = sim_state.hands[current]

            # Use inner decider
            sim_view = PlayerView(sim_state, current)
            if self.inner:
                play_cards = self.inner.choose_play(sim_view)
            else:
                from .agent import BlindAgent
                play_cards = BlindAgent().choose_play(sim_view)

            rules = RulesEngine(sim_state.level)
            result = rules.validate_play(
                cards=play_cards, hand=hand,
                table_state=sim_state.table,
                player_id=current,
                finished_positions=sim_state.finished_positions)

            if not result.is_legal:
                play_cards = []

            if play_cards:
                apply_play(sim_state, current, result.resolved_combo)
            else:
                sim_state.table.record_pass(current)
                sim_state.current_player = (current + 1) % 4

        return did_team_win(sim_state, my_team)
