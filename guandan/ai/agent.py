"""AI agents for Guandan.

HeuristicAgent: greedy play based on hand-quality scoring.
MonteCarloAgent: sample opponent hands, simulate rollout, pick best play.

Architecture:
  Layer 0: hand_eval.py + opponent.py  — analysis
  Layer 1: combo_finder.py             — candidate generation (existing)
  Layer 2: scorer.py                   — play evaluation
  Layer 3: agent.py                    — decision
"""

from __future__ import annotations

import math
import random
import time
from typing import List, Optional, Tuple

from ..card import Card, Rank, effective_rank
from ..combo import Combo, ComboType
from ..combo_finder import ComboFinder
from ..combo_compare import can_beat
from ..deck import Deck
from ..game_state import GameState
from ..rules import RulesEngine
from ..score import calculate_result
from ..state_utils import clone_state, apply_play, start_new_trick, next_active_player, did_team_win
from .scorer import choose_best_play, score_play
from .opponent import CardCounter
from ..logging import ai_log
from .player_view import PlayerView
from .params import AIParams, DEFAULT_PARAMS


# ==================================================================
# Base agent
# ==================================================================

class BaseAgent:
    """Abstract base for all agents."""

    def choose_play(self, view: PlayerView) -> List[Card]:
        raise NotImplementedError


def _enumerate_straight_flush_responses(hand: Tuple[Card, ...], table_combo: Combo,
                                        parser: ComboParser, level: int) -> List[Combo]:
    """The best SF strictly beating the table SF, capped to one candidate.

    Capped on purpose: MC/ISMCTS simulate every candidate; SF responses are
    rare and one option (highest end rank) is decision-sufficient.
    """
    return _sf_candidates_above(hand, table_combo, parser, level)[:1]


def _sf_candidates_above(hand: Tuple[Card, ...], table_combo: Combo,
                         parser: ComboParser, level: int) -> List[Combo]:
    """All straight flushes (any suit) strictly above table_combo, best first."""
    from ..card import Suit
    suits = (Suit.CLUBS, Suit.DIAMONDS, Suit.HEARTS, Suit.SPADES)
    length = table_combo.length
    t_eff = effective_rank(table_combo.main_rank, level)
    normals = [c for c in hand if not c.is_wild(level)]
    wilds = [c for c in hand if c.is_wild(level)]
    wild_count = len(wilds)

    results = []
    for suit in suits:
        for end in range(3 + length - 1, 15):
            start = end - length + 1
            if start < 3 or end <= table_combo.main_rank.value:
                continue
            subset = []
            missing = 0
            ok = True
            for rv in range(start, end + 1):
                matches = [c for c in normals
                           if c.rank.value == rv and c.suit == suit]
                if matches:
                    subset.append(matches[0])
                elif suit == Suit.HEARTS and missing < wild_count:
                    missing += 1  # wild (always hearts) fills the gap
                else:
                    ok = False
                    break
            if not ok:
                continue
            parsed = parser.parse(subset + wilds[:missing])
            if parsed and parsed.combo_type == ComboType.STRAIGHT_FLUSH:
                if effective_rank(parsed.main_rank, level) > t_eff:
                    results.append(parsed)
    results.sort(key=lambda c: effective_rank(c.main_rank, level), reverse=True)
    return results


def _enumerate_responses(hand: Tuple[Card, ...], table_combo: Combo,
                         finder: ComboFinder, level: int) -> List[Combo]:
    """Enumerate ALL valid same-type responses + bomb option (+ SF options).

    Rule fidelity (江苏竞赛规则): a straight flush beats ALL normal bombs
    and every non-bomb combo, so SF candidates are appended for ANY table
    combo below SF. When the table combo IS a straight flush, only higher
    SFs are legal (rockets are validated by can_beat at play time).
    """
    from ..combo_parser import ComboParser
    parser = ComboParser(level)
    candidates = []
    ct = table_combo.combo_type
    wilds = [c for c in hand if c.is_wild(level)]
    normals = [c for c in hand if not c.is_wild(level)]

    if ct == ComboType.STRAIGHT_FLUSH:
        candidates.extend(_enumerate_straight_flush_responses(
            hand, table_combo, parser, level))
    elif ct.name == 'SINGLE':
        t_eff = effective_rank(table_combo.main_rank, level)
        count = 0
        for c in sorted(hand, key=lambda x: effective_rank(x.rank, level)):
            if effective_rank(c.rank, level) > t_eff:
                parsed = parser.parse([c])
                if parsed: candidates.append(parsed); count += 1
                if count >= 5: break  # limit: 5 singles max
    elif ct.name == 'PAIR':
        t_eff = effective_rank(table_combo.main_rank, level)
        by_rank: dict = {}
        for c in hand: by_rank.setdefault(c.rank, []).append(c)
        count = 0
        for rank, cards in sorted(by_rank.items(), key=lambda x: effective_rank(x[0], level)):
            if effective_rank(rank, level) > t_eff and len(cards) >= 2:
                parsed = parser.parse(list(cards[:2]))
                if parsed: candidates.append(parsed); count += 1
                if count >= 4: break
    elif ct.name == 'TRIPLE':
        t_eff = effective_rank(table_combo.main_rank, level)
        by_rank: dict = {}
        for c in hand: by_rank.setdefault(c.rank, []).append(c)
        count = 0
        for rank, cards in sorted(by_rank.items(), key=lambda x: effective_rank(x[0], level)):
            if effective_rank(rank, level) > t_eff and len(cards) >= 3:
                parsed = parser.parse(list(cards[:3]))
                if parsed: candidates.append(parsed); count += 1
                if count >= 3: break
    elif ct.name == 'TRIPLE_PAIR':
        t_eff = effective_rank(table_combo.main_rank, level)
        by_rank: dict = {}
        for c in hand: by_rank.setdefault(c.rank, []).append(c)
        for rank, cards in by_rank.items():
            if effective_rank(rank, level) > t_eff and len(cards) >= 3:
                # Need a pair of different rank for the side
                for r2, cs2 in by_rank.items():
                    if r2 != rank and len(cs2) >= 2:
                        parsed = parser.parse(list(cards[:3]) + list(cs2[:2]))
                        if parsed: candidates.append(parsed)
    elif ct.name in ('STRAIGHT', 'STRAIGHT_FLUSH'):
        if effective_rank(table_combo.main_rank, level) >= 15:
            pass  # level card or joker: no same-type response possible
        else:
            length = table_combo.length
            t_end = table_combo.main_rank.value
            for end in range(t_end + 1, 15):
                start = end - length + 1
                if start < 3: continue
                needed = 0; subset = []
                for rv in range(start, end + 1):
                    matches = [c for c in normals if c.rank.value == rv]
                    if matches: subset.append(matches[0])
                    else: needed += 1
                if needed == len(wilds) and len(subset) + needed == length:
                    parsed = parser.parse(subset + wilds)
                    if parsed: candidates.append(parsed)
    elif ct.name == 'CONSECUTIVE_PAIRS':
        if effective_rank(table_combo.main_rank, level) >= 15:
            pass
        else:
            num_pairs = table_combo.length // 2
            t_end = table_combo.main_rank.value
            by_rank: dict = {}
            for c in normals: by_rank.setdefault(c.rank, []).append(c)
            for end in range(t_end + 1, 15):
                start = end - num_pairs + 1
                if start < 3: continue
                needed = 0; subset = []
                for rv in range(start, end + 1):
                    cs = by_rank.get(Rank(rv), [])
                    available = min(len(cs), 2)
                    subset.extend(cs[:available])
                    needed += max(0, 2 - available)
                if needed == len(wilds):
                    parsed = parser.parse(subset + wilds)
                    if parsed: candidates.append(parsed)
    elif ct.name == 'CONSECUTIVE_TRIPLES':
        if effective_rank(table_combo.main_rank, level) >= 15:
            pass
        else:
            num_triples = table_combo.length // 3
            t_end = table_combo.main_rank.value
            by_rank: dict = {}
            for c in normals: by_rank.setdefault(c.rank, []).append(c)
            for end in range(t_end + 1, 15):
                start = end - num_triples + 1
                if start < 3: continue
                needed = 0; subset = []
                for rv in range(start, end + 1):
                    cs = by_rank.get(Rank(rv), [])
                    available = min(len(cs), 3)
                    subset.extend(cs[:available])
                    needed += max(0, 3 - available)
                if needed == len(wilds):
                    parsed = parser.parse(subset + wilds)
                    if parsed: candidates.append(parsed)
    else:
        resp = finder.pick_response(table_combo)
        if resp: candidates.append(resp)

    # Straight flush beats every normal bomb and every non-bomb combo — add
    # SF candidates whenever the table combo is below SF (SF/rocket excluded).
    # SF outranks any single/bomb rank, so every found SF is legal here.
    if ct != ComboType.STRAIGHT_FLUSH and ct != ComboType.ROCKET:
        candidates.extend(_find_straight_flush_leads(hand, parser, level))

    # Add bomb (must be able to beat the table combo)
    bomb = finder._find_any_bomb()
    if bomb and can_beat(bomb, table_combo):
        candidates.append(bomb)
    return candidates


def _find_straight_flush_leads(hand: Tuple[Card, ...], parser: ComboParser,
                               level: int) -> List[Combo]:
    """Find the single best straight flush in hand (highest effective rank).

    Capped to one candidate on purpose: MC/ISMCTS simulate every candidate,
    and enumerating all SF windows (up to ~15) blows the decision time budget.
    One SF option is decision-sufficient — SF outranks everything below it,
    and the highest end is the hardest for opponents to top.
    """
    from ..card import Suit
    normals = [c for c in hand if not c.is_wild(level)]
    wilds = [c for c in hand if c.is_wild(level)]
    by_suit: dict = {}
    for c in normals:
        if c.rank not in (Rank.TWO, Rank.SMALL_JOKER, Rank.BIG_JOKER):
            by_suit.setdefault(c.suit, set()).add(c.rank.value)

    wild_count = len(wilds)
    best = None
    best_eff = -1

    for suit, ranks_present in by_suit.items():
        if len(ranks_present) + wild_count < 5:
            continue
        for start in range(3, 11):  # 5-run: start 3..10, end <= 14
            end = start + 4
            window = set(range(start, end + 1))
            present = window & ranks_present
            missing = 5 - len(present)
            if missing > wild_count:
                continue
            subset = []
            for rv in sorted(window):
                matches = [c for c in normals
                           if c.rank.value == rv and c.suit == suit]
                if matches:
                    subset.append(matches[0])
            needed = 5 - len(subset)
            if needed > wild_count:
                continue
            parsed = parser.parse(subset + wilds[:needed])
            if parsed and parsed.combo_type == ComboType.STRAIGHT_FLUSH:
                from ..card import effective_rank as _eff
                eff = _eff(parsed.main_rank, level)
                if eff > best_eff:
                    best, best_eff = parsed, eff
    return [best] if best else []


def _generate_lead_candidates(finder: ComboFinder, hand: Tuple[Card, ...]) -> List[Combo]:
    """Generate a diverse set of lead candidates WITHOUT calling find_all()."""
    from ..combo_parser import ComboParser
    parser = ComboParser(finder.level)
    candidates: List[Combo] = []
    seen: set = set()

    def add(c: Combo):
        key = (c.combo_type, c.main_rank, c.length)
        if key not in seen:
            seen.add(key)
            candidates.append(c)

    normals = [c for c in hand if not c.is_wild(finder.level)]
    wilds_list = [c for c in hand if c.is_wild(finder.level)]
    by_rank: dict = {}
    for c in hand:
        by_rank.setdefault(c.rank, []).append(c)

    # Singles: lowest ranks (up to 5)
    singles = sorted(hand, key=lambda c: (c.rank.value, c.suit.value))
    for c in singles[:5]:
        parsed = parser.parse([c])
        if parsed:
            add(parsed)

    # Pairs: all possible pairs (sorted by rank, up to 5)
    pair_count = 0
    for rank, cards in sorted(by_rank.items(), key=lambda x: x[0].value):
        if len(cards) >= 2:
            parsed = parser.parse(list(cards[:2]))
            if parsed: add(parsed); pair_count += 1
            if pair_count >= 5: break

    # Triples: all (up to 3)
    triple_count = 0
    for rank, cards in sorted(by_rank.items(), key=lambda x: x[0].value):
        if len(cards) >= 3:
            parsed = parser.parse(list(cards[:3]))
            if parsed: add(parsed); triple_count += 1
            if triple_count >= 3: break

    # Triple+pair: 三带二
    for rank, cards in sorted(by_rank.items(), key=lambda x: x[0].value):
        if len(cards) >= 3:
            for r2, cs2 in by_rank.items():
                if r2 != rank and len(cs2) >= 2:
                    parsed = parser.parse(list(cards[:3]) + list(cs2[:2]))
                    if parsed: add(parsed)

    # Straights: try lengths 5,6,7
    for length in [5, 6, 7]:
        found_straight = False
        for end in range(length + 2, 15):
            start = end - length + 1
            if start < 3: continue
            needed = 0; subset = []
            for r_val in range(start, end + 1):
                matches = [c for c in normals if c.rank.value == r_val]
                if matches: subset.append(matches[0])
                else: needed += 1
            if needed == len(wilds_list) and len(subset) + needed == length:
                parsed = parser.parse(subset + wilds_list)
                if parsed and parsed.combo_type.name in ('STRAIGHT', 'STRAIGHT_FLUSH'):
                    add(parsed); found_straight = True; break
        if found_straight: break

    # Consecutive pairs (连对): 3+ consecutive ranks with ≥2 cards each
    for num_pairs in [3, 4, 5]:
        for end in range(num_pairs + 2, 15):
            start = end - num_pairs + 1
            if start < 3: continue
            needed = 0; subset = []
            for r_val in range(start, end + 1):
                cs = by_rank.get(Rank(r_val), [])
                available = min(len(cs), 2)
                subset.extend(cs[:available])
                needed += max(0, 2 - available)
            if needed <= len(wilds_list):
                total_cards = subset + wilds_list[:needed]
                parsed = parser.parse(total_cards)
                if parsed and parsed.combo_type.name == 'CONSECUTIVE_PAIRS':
                    add(parsed)
                    break
        else: continue
        break

    # Consecutive triples (钢板): 2+ consecutive ranks with ≥3 cards each
    for num_triples in [2, 3]:
        for end in range(num_triples + 2, 15):
            start = end - num_triples + 1
            if start < 3: continue
            needed = 0; subset = []
            for r_val in range(start, end + 1):
                cs = by_rank.get(Rank(r_val), [])
                available = min(len(cs), 3)
                subset.extend(cs[:available])
                needed += max(0, 3 - available)
            if needed <= len(wilds_list):
                total_cards = subset + wilds_list[:needed]
                parsed = parser.parse(total_cards)
                if parsed and parsed.combo_type.name == 'CONSECUTIVE_TRIPLES':
                    add(parsed)
                    break
        else: continue
        break

    # Bombs
    for rank, cards in sorted(by_rank.items(), key=lambda x: x[0].value):
        nc = len(cards)
        total = nc + len(wilds_list)
        if total >= 4:
            size = min(total, 8)
            wild_need = max(0, size - nc)
            all_cards = list(cards[:size - wild_need]) + wilds_list[:wild_need]
            parsed = parser.parse(all_cards)
            if parsed and parsed.is_bomb:
                add(parsed)

    # Rocket
    big = [c for c in hand if c.rank == Rank.BIG_JOKER]
    small = [c for c in hand if c.rank == Rank.SMALL_JOKER]
    if len(big) >= 2 and len(small) >= 2:
        parsed = parser.parse(big[:2] + small[:2])
        if parsed:
            add(parsed)

    return candidates


class HeuristicAgent(BaseAgent):
    """Picks the play with the best immediate heuristic score."""

    def __init__(self, params: AIParams = DEFAULT_PARAMS, **kw):
        self.params = params

    def choose_play(self, view: PlayerView) -> List[Card]:
        t0 = time.time()
        pid = view.player_id
        hand = view.my_hand
        table = view.table
        level = view.level
        finder = ComboFinder(hand, level)

        # Log the AI's view
        ai_log(pid, "decision_start", view=view.to_json())

        # Generate candidates — use FAST methods only
        if table.is_empty or table.last_played_player == pid:
            candidates = _generate_lead_candidates(finder, hand)
        else:
            candidates = []
            combo = finder.pick_response(table.current_combo)
            if combo:
                candidates.append(combo)
            bomb = finder._find_any_bomb()
            if bomb and bomb not in candidates:
                candidates.append(bomb)

        can_pass = (not table.is_empty and table.last_played_player != pid)

        # Score candidates
        scored = []
        for c in candidates:
            used_ids = {x.id for x in c.cards}
            hand_after = tuple(x for x in hand if x.id not in used_ids)
            s = score_play(c, hand, hand_after, table.current_combo, level, self.params)
            scored.append((c, s))

        scored.sort(key=lambda x: x[1], reverse=True)

        # Log candidates
        ai_log(pid, "candidates", candidates=[
            {
                "type": c.combo_type.name,
                "cards": [x.display for x in c.cards],
                "length": c.length,
                "main_rank": c.main_rank.name,
                "score": round(s, 2),
            }
            for c, s in scored[:15]
        ])

        best = choose_best_play(candidates, hand, table.current_combo, level, can_pass, self.params)

        elapsed = (time.time() - t0) * 1000
        ai_log(pid, "decision_end",
               choice=("pass" if best is None else f"{best.combo_type.name} {[c.display for c in best.cards]}"),
               elapsed_ms=round(elapsed, 1))

        if best is None:
            return []
        return list(best.cards)


# ==================================================================
# BlindAgent — simulation-only, weight-based, ignores opponent hands
# ==================================================================

class BlindAgent(BaseAgent):
    """Blind agent for MC simulations — scores by AIParams weights.

    Does NOT inspect opponent hands. Tunable via 12 AIParams weights.
    """

    def __init__(self, params: AIParams = DEFAULT_PARAMS):
        self.params = params

    def choose_play(self, view: PlayerView) -> List[Card]:
        hand = view.my_hand
        table = view.table
        pid = view.player_id
        level = view.level
        p = self.params
        finder = ComboFinder(hand, level)

        if table.is_empty or table.last_played_player == pid:
            candidates = _generate_lead_candidates(finder, hand)
            table_combo = None
        else:
            candidates = []
            combo = finder.pick_response(table.current_combo)
            if combo: candidates.append(combo)
            bomb = finder._find_any_bomb()
            if bomb and bomb not in candidates:
                candidates.append(bomb)
            table_combo = table.current_combo

        can_pass = (table_combo is not None and table.last_played_player != pid)

        best = None
        best_score = float('-inf')
        total = len(hand)

        for c in candidates:
            eff = (c.length / total) * p.efficiency_weight if total > 0 else 0
            bomb_pen = 0.0
            if c.is_bomb:
                if table_combo is None:
                    bomb_pen = p.bomb_lead_penalty
                elif not table_combo.is_bomb:
                    bomb_pen = p.bomb_overuse_penalty
                else:
                    bomb_pen = p.bomb_vs_bomb_bonus
            usage = c.length * p.card_usage_weight
            pos = p.lead_bonus if table_combo is None else (p.follow_bonus if not c.is_bomb else 0)
            # Per-card penalty for leading high-value cards
            if table_combo is None:
                rv = effective_rank(c.main_rank, level)
                rank_card_count = sum(1 for x in c.cards if x.rank == c.main_rank)
                if rv >= 16:  # jokers
                    pos += p.joker_lead_penalty * max(1, rank_card_count)
                elif rv == 15:  # level card (级牌)
                    pos += p.rank_card_lead_penalty * max(1, rank_card_count)
                elif rv >= 13:  # K, A
                    pos += p.high_rank_lead_penalty
            s = eff + bomb_pen + usage + pos
            if s > best_score:
                best_score = s
                best = c

        if best is not None and best_score >= p.pass_threshold:
            return list(best.cards)
        if can_pass and random.random() > p.sim_pass_prob:
            return []
        if best is not None:
            return list(best.cards)
        if can_pass:
            return []  # no legal response — must pass, not play an illegal single
        if hand:
            return [hand[0]]
        return []


# ==================================================================
# MonteCarloAgent — sample + simulate
# ==================================================================

class MonteCarloAgent(BaseAgent):
    """Monte Carlo agent with opponent hand sampling and rollout simulation."""

    def score_candidates(self, view: PlayerView, candidates: List[Combo],
                         can_pass: bool = False) -> List[Tuple[Combo | None, float]]:
        """Score a pre-defined list of candidates. Returns [(candidate, win_rate), ...].

        None represents PASS. Used by the suggest API to evaluate specific plays.
        """
        import time
        t_start = time.time()
        pid = view.player_id
        counter = CardCounter(view.my_hand)
        for p in range(4):
            if p != pid:
                counter.set_opponent_hand_sizes({
                    p: view.opponent_hand_size(p) for p in range(4) if p != pid
                })
        for c in view.played_cards:
            counter._seen.add(c.id)

        all_candidates = list(candidates)
        if can_pass:
            all_candidates.append(None)

        results = []
        for candidate in all_candidates:
            if time.time() - t_start > self.time_limit_ms / 1000:
                break
            wins = 0
            sims = 0
            for _ in range(self.num_samples):
                if time.time() - t_start > self.time_limit_ms / 1000:
                    break
                if self._run_simulation(view, candidate, counter):
                    wins += 1
                sims += 1
            wr = wins / sims if sims > 0 else 0.0
            results.append((candidate, wr))

        return results

    def __init__(self, num_samples: int = 50, time_limit_ms: float = 3000,
                 params: AIParams = DEFAULT_PARAMS, config: dict = None):
        self.num_samples = num_samples
        self.time_limit_ms = time_limit_ms
        self.params = params
        self._config = config  # for simulation model selection

    def choose_play(self, view: PlayerView) -> List[Card]:
        t_start = time.time()
        pid = view.player_id
        hand = view.my_hand
        table = view.table
        level = view.level
        ai_log(pid, "decision_start",
               agent="MonteCarlo",
               hand_size=len(hand),
               num_samples=self.num_samples,
               time_limit_ms=self.time_limit_ms)
        finder = ComboFinder(hand, level)

        # Generate candidates — same enumeration as suggest endpoint
        if table.is_empty or table.last_played_player == pid:
            candidates = _generate_lead_candidates(finder, hand)
        else:
            candidates = _enumerate_responses(hand, table.current_combo, finder, level)

        can_pass = (not table.is_empty and table.last_played_player != pid)

        if not candidates and not can_pass:
            return []
        if not candidates:
            return []

        # Add "pass" as a candidate
        all_candidates = list(candidates)
        if can_pass:
            all_candidates.append(None)  # None = pass

        # Build card counter from current view
        counter = CardCounter(hand)
        for p in range(4):
            if p != pid:
                counter.set_opponent_hand_sizes({
                    p: view.opponent_hand_size(p) for p in range(4) if p != pid
                })
        for c in view.played_cards:
            counter._seen.add(c.id)

        # Score each candidate
        results = []
        timed_out = False
        for candidate in all_candidates:
            if candidate is None:
                results.append((None, 0.0))
                continue

            if time.time() - t_start > self.time_limit_ms / 1000:
                timed_out = True
                break

            wins = 0
            simulations = 0
            for _ in range(self.num_samples):
                if time.time() - t_start > self.time_limit_ms / 1000:
                    timed_out = True
                    break
                sim_result = self._run_simulation(
                    view, candidate, counter
                )
                if sim_result:
                    wins += 1
                simulations += 1

            win_rate = wins / simulations if simulations > 0 else 0.0
            results.append((candidate, win_rate))

        if not results:
            return []

        best = max(results, key=lambda r: r[1])
        best_wr = best[1]
        elapsed = (time.time() - t_start) * 1000
        choice_cards = [x.display for x in best[0].cards] if best[0] else []
        ai_log(pid, "decision_end",
               agent="MonteCarlo",
               candidates_scored=[{
                   "type": (c.combo_type.name if c else "PASS"),
                   "cards": [x.display for x in c.cards] if c else [],
                   "win_rate": round(wr, 3),
               } for c, wr in results],
               choice=(f"{best[0].combo_type.name}" if best[0] else "PASS"),
               choice_cards=choice_cards,
               choice_win_rate=round(best_wr, 3),
               elapsed_ms=round(elapsed, 1),
               timed_out=timed_out,
               samples_done=sum(1 for _ in results))
        if best[0] is None:
            return []
        return list(best[0].cards)

    def _run_simulation(
        self,
        view: PlayerView,
        my_play: Combo,
        counter: CardCounter,
    ) -> bool:
        """Run one simulation from the current state.

        Returns True if my team wins.
        """
        my_id = view.player_id
        my_team = {0: 0, 1: 1, 2: 0, 3: 1}[my_id]
        sim_state = clone_state(view._state)

        # Apply my play (or pass)
        if my_play is not None:
            apply_play(sim_state, my_id, my_play)
        else:
            sim_state.table.record_pass(my_id)
            sim_state.current_player = (my_id + 1) % 4

        if len(sim_state.finished_positions) >= 3:
            return did_team_win(sim_state, my_team)

        # Deal unseen cards using tracker (constrained sampling)
        if view.tracker is not None:
            sampled = view.tracker.sample_opponent_hands()
            for pid in range(4):
                if pid == my_id:
                    continue
                sim_state.hands = tuple(
                    tuple(sampled.get(pid, [])) if i == pid else h
                    for i, h in enumerate(sim_state.hands)
                )
        else:
            # Fallback: random deal
            my_card_ids = {c.id for c in view.my_hand}
            played_ids = {c.id for c in view.played_cards}
            pool = [Card.from_id(i) for i in range(108)
                    if i not in my_card_ids and i not in played_ids]
            random.shuffle(pool)
            idx = 0
            for pid in range(4):
                if pid == my_id:
                    continue
                size = view.opponent_hand_size(pid)
                sim_state.hands = tuple(
                    tuple(pool[idx:idx + size]) if i == pid else h
                    for i, h in enumerate(sim_state.hands)
                )
                idx += size
        # Simulate remainder using configured simulation model
        sim_agent = self._make_sim_agent(my_id)
        agents = [sim_agent for _ in range(4)]

        for _ in range(200):  # safety limit
            if len(sim_state.finished_positions) >= 3:
                return did_team_win(sim_state, my_team)

            table = sim_state.table
            if table.last_played_player >= 0:
                other_active = [p for p in sim_state.active_players
                                if p != table.last_played_player]
                if table.pass_count >= len(other_active):
                    sim_state.current_player = table.last_played_player
                    start_new_trick(sim_state)
                    continue

            current = next_active_player(sim_state, sim_state.current_player)
            agent = agents[current]
            sim_hand = sim_state.hands[current]
            # InformedAgent needs full state; others use PlayerView
            if isinstance(agent, InformedAgent):
                play_cards = agent.choose_play_full(sim_state, current)
            else:
                sim_view = PlayerView(sim_state, current)
                play_cards = agent.choose_play(sim_view)

            # Validate
            rules = RulesEngine(sim_state.level)
            result = rules.validate_play(
                cards=play_cards, hand=sim_hand,
                table_state=sim_state.table,
                player_id=current,
                finished_positions=sim_state.finished_positions,
            )

            if not result.is_legal:
                play_cards = []

            if play_cards:
                apply_play(sim_state, current, result.resolved_combo)
            else:
                sim_state.table.record_pass(current)
                sim_state.current_player = (current + 1) % 4

        # Fallback: check result
        return did_team_win(sim_state, my_team)

    def _make_sim_agent(self, player_id=None):
        """Create a simulation agent based on config."""
        from .registry import create_simulation_agent
        if self._config:
            return create_simulation_agent(self._config, player_id)
        return InformedAgent()


# Module-level helpers (non-duplicated, kept for backward compat until Phase 5)

def _deal_subset(unseen: List[Card], start: int, count: int, counter: CardCounter) -> Tuple[Card, ...]:
    """Deal a subset of unseen cards, keeping known cards in place."""
    # This is simplified — in reality we'd need to preserve already-known cards
    # For now, deal from the unseen pool
    end = min(start + count, len(unseen))
    return tuple(unseen[start:end])


def _select_diverse_candidates(candidates: List[Combo], limit: int) -> List[Combo]:
    """Select a diverse subset: include different combo types and sizes."""
    by_type: dict = {}
    for c in candidates:
        t = c.combo_type
        if t not in by_type:
            by_type[t] = []
        by_type[t].append(c)

    result = []
    # Take up to 3 from each type, prioritizing different main_ranks
    for t, combos in by_type.items():
        combos.sort(key=lambda c: c.main_rank)
        seen_ranks = set()
        for c in combos:
            if len(result) >= limit:
                return result
            if c.main_rank not in seen_ranks or len(by_type[t]) <= 3:
                result.append(c)
                seen_ranks.add(c.main_rank)
    return result[:limit]


# ==================================================================
# ==================================================================
# InformedAgent — simulation-only, exploits known opponent hands, no tunable weights
# ==================================================================

class InformedAgent(BaseAgent):
    """Informed agent for MC simulations — exploits known opponent hands.

    No tunable weights — checks who can beat what directly.
    """

    def choose_play(self, view: PlayerView) -> List[Card]:
        # Fallback: shouldn't be called directly
        return self.choose_play_full(view._state, view.player_id)

    def choose_play_full(self, state: GameState, player_id: int) -> List[Card]:
        hand = state.hands[player_id]
        table = state.table
        level = state.level
        finder = ComboFinder(hand, level)

        # Team mapping
        my_team = {0: 0, 1: 1, 2: 0, 3: 1}[player_id]
        partner = (player_id + 2) % 4

        # Generate candidates
        if table.is_empty or table.last_played_player == player_id:
            candidates = _generate_lead_candidates(finder, hand)
            table_combo = None
        else:
            candidates = _enumerate_responses(hand, table.current_combo, finder, level)
            table_combo = table.current_combo

        can_pass = table_combo is not None and table.last_played_player != player_id

        best = None
        best_score = float('-inf')

        for c in candidates:
            s = self._score_full_info(c, hand, state, player_id, my_team, partner)
            if s > best_score:
                best_score = s
                best = c

        # Pass decision
        if can_pass:
            pass_score = self._score_pass(state, player_id, my_team)
            if pass_score >= best_score or best is None:
                return []

        if best is not None:
            return list(best.cards)
        if hand:
            return [hand[0]]
        return []

    def _score_full_info(self, candidate: Combo, hand: Tuple[Card, ...],
                         state: GameState, player_id: int,
                         my_team: int, partner: int) -> float:
        """Score a candidate using full known information. No tunable weights."""
        level = state.level

        # 1. My rounds saved
        rounds_me_before = estimate_rounds_for_player(state, player_id)
        hand_after = _remove_cards(hand, candidate.cards)
        rounds_me_after = estimate_rounds_given_hand(hand_after, state, player_id)
        my_rounds_saved = rounds_me_before - rounds_me_after

        # 2. Can the next active player beat this? (deterministic: we know their hand)
        next_player = next_active_player(state, (player_id + 1) % 4)
        can_be_beaten_by = []
        cannot_be_beaten_by = []
        for p in range(4):
            if p == player_id or p in state.finished_positions:
                continue
            if _can_player_beat(state, p, candidate):
                can_be_beaten_by.append(p)
            else:
                cannot_be_beaten_by.append(p)

        # 3. Team vs opponent rounds impact
        team_rounds = 0
        opp_rounds = 0
        for p in range(4):
            if p in state.finished_positions:
                continue
            r = estimate_rounds_for_player(state, p)
            if {0: 0, 1: 1, 2: 0, 3: 1}[p] == my_team:
                team_rounds += r
            else:
                opp_rounds += r

        # 4. Score
        score = my_rounds_saved * 5.0

        # If no one can beat it → huge bonus (I keep control)
        if not can_be_beaten_by:
            score += 8.0
        else:
            # Someone will beat it → small penalty
            # If it's a teammate who beats it, that's fine
            teammates_beating = [p for p in can_be_beaten_by
                                if {0: 0, 1: 1, 2: 0, 3: 1}[p] == my_team]
            if teammates_beating:
                score += 2.0  # teammate can cover
            else:
                score -= 3.0  # opponent will take control

        # 5. Bomb usage
        if candidate.is_bomb:
            table_combo = state.table.current_combo
            if table_combo is None:
                score -= 5.0
            elif not table_combo.is_bomb:
                # Quick check: does hand have any non-bomb that beats the table?
                finder = ComboFinder(hand, state.level)
                resp = finder.pick_response(table_combo)
                if resp and not resp.is_bomb:
                    score -= 4.0  # have cheaper option

        # 6. Team rounds advantage
        rounds_diff = opp_rounds - team_rounds
        score += rounds_diff * 1.0

        return score

    def _score_pass(self, state: GameState, player_id: int, my_team: int) -> float:
        """Score the pass option."""
        # Passing is neutral for my rounds
        # It might help if a teammate can take control
        table_combo = state.table.current_combo
        if table_combo is None:
            return 0.0

        # Check if a teammate can beat the current combo
        partner = (player_id + 2) % 4
        for p in [partner]:
            if p not in state.finished_positions:
                if _can_player_beat(state, p, table_combo):
                    return 3.0  # teammate can handle it

        # Check if any opponent can beat it (they already have control)
        last_player = state.table.last_played_player
        if {0: 0, 1: 1, 2: 0, 3: 1}.get(last_player) == my_team:
            return 2.0  # my team already has control, passing is fine

        return 0.0  # neutral


# ==================================================================
# Perfect-info helpers
# ==================================================================

def _remove_cards(hand: Tuple[Card, ...], cards: Tuple[Card, ...]) -> Tuple[Card, ...]:
    ids = {c.id for c in cards}
    return tuple(c for c in hand if c.id not in ids)


def estimate_rounds_for_player(state: GameState, player_id: int) -> int:
    """Fast round estimate: ~hand_size/2 (most combos are 2+ cards)."""
    return max(1, len(state.hands[player_id]) // 2)


def estimate_rounds_given_hand(hand: Tuple[Card, ...], state: GameState,
                               player_id: int = 0) -> int:
    """Fast round estimate for a specific hand."""
    return max(0, len(hand) // 2)


def _can_player_beat(state: GameState, player_id: int, combo: Combo) -> bool:
    """Check if a player can beat a given combo (full information)."""
    from ..combo_finder import ComboFinder
    from ..combo_compare import can_beat
    hand = state.hands[player_id]
    finder = ComboFinder(hand, state.level)
    resp = finder.pick_response(combo)
    if resp and can_beat(resp, combo):
        return True
    bomb = finder._find_any_bomb()
    return bomb is not None and can_beat(bomb, combo)


# ==================================================================
# Legacy agents (kept for compatibility)
# ==================================================================

class RandomAgent(BaseAgent):
    """Random legal play — for testing."""

    def __init__(self, params: AIParams = DEFAULT_PARAMS, **kw):
        pass

    def choose_play(self, view: PlayerView) -> List[Card]:
        hand = view.my_hand
        table = view.table
        pid = view.player_id
        finder = ComboFinder(hand, view.level)

        if table.is_empty or table.last_played_player == pid:
            combo = finder.pick_lead()
        else:
            combo = finder.pick_response(table.current_combo)
            if combo is None:
                return []
            if random.random() < 0.3:
                return []
        if combo:
            return list(combo.cards)
        return []


class FirstPlayAgent(BaseAgent):
    """Always plays first found legal combo — for testing."""

    def __init__(self, params: AIParams = DEFAULT_PARAMS, **kw):
        pass

    def choose_play(self, view: PlayerView) -> List[Card]:
        hand = view.my_hand
        table = view.table
        pid = view.player_id
        finder = ComboFinder(hand, view.level)

        if table.is_empty or table.last_played_player == pid:
            combo = finder.pick_lead()
        else:
            combo = finder.pick_response(table.current_combo)
        if combo:
            return list(combo.cards)
        return []


class GreedyAgent(BaseAgent):
    """Plays the combo that uses the most cards."""

    def __init__(self, params: AIParams = DEFAULT_PARAMS, **kw):
        pass

    def choose_play(self, view: PlayerView) -> List[Card]:
        hand = view.my_hand
        table = view.table
        pid = view.player_id
        finder = ComboFinder(hand, view.level)

        if table.is_empty or table.last_played_player == pid:
            combos = _generate_lead_candidates(finder, hand)
            if combos:
                best = max(combos, key=lambda c: (c.length, c.main_rank))
                return list(best.cards)
            return []
        combo = finder.pick_response(table.current_combo)
        if combo:
            return list(combo.cards)
        return []
