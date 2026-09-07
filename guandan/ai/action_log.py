"""Action log and card tracker — the AI's memory.

ActionLog: append-only record of every play and pass.
CardTracker: derived statistics for opponent modeling.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from ..card import Card, Rank, effective_rank
from ..combo import Combo, ComboType


@dataclass
class Action:
    """One recorded action in the game."""
    seq: int
    trick_id: int
    player: int
    type: str               # "play" | "pass"
    cards: Tuple[Card, ...]  # empty for pass
    combo: Optional[Combo]   # None for pass
    passed_on: Optional[Combo] = None  # what combo was on the table when they passed


class ActionLog:
    """Append-only log of all game actions."""

    def __init__(self):
        self._actions: List[Action] = []
        self._seq = 0

    def record_play(self, trick_id: int, player: int, cards: List[Card], combo: Combo):
        self._actions.append(Action(
            seq=self._seq, trick_id=trick_id, player=player,
            type="play", cards=tuple(cards), combo=combo,
        ))
        self._seq += 1

    def record_pass(self, trick_id: int, player: int, table_combo: Optional[Combo]):
        self._actions.append(Action(
            seq=self._seq, trick_id=trick_id, player=player,
            type="pass", cards=(), combo=None, passed_on=table_combo,
        ))
        self._seq += 1

    def __len__(self) -> int:
        return len(self._actions)

    def __iter__(self):
        return iter(self._actions)

    def __getitem__(self, i: int) -> Action:
        return self._actions[i]

    @property
    def actions(self) -> List[Action]:
        return list(self._actions)

    def played_cards_by(self, player: int) -> List[Card]:
        """All cards played by a specific player."""
        result = []
        for a in self._actions:
            if a.type == "play" and a.player == player:
                result.extend(a.cards)
        return result

    def played_count(self, player: int) -> int:
        """How many cards this player has tabled (public info)."""
        return sum(len(a.cards) for a in self._actions
                   if a.type == "play" and a.player == player)

    def pass_count(self, player: int) -> int:
        """How many times this player passed (public info)."""
        return sum(1 for a in self._actions
                   if a.type == "pass" and a.player == player)

    def recent(self, n: int) -> List[Action]:
        return self._actions[-n:] if n < len(self._actions) else list(self._actions)


class CardTracker:
    """Derived statistics from ActionLog + own hand."""

    def __init__(self, own_hand: Tuple[Card, ...], log: ActionLog, level: int = 2,
                 viewer_pid: int = 0):
        # All my cards
        self._my_card_ids: Set[int] = {c.id for c in own_hand}
        self._my_hand = own_hand
        self._level = level
        # The seat whose memory this tracker represents. Opponent sets are
        # derived from it — hardcoding [1,2,3] silently blanked seat 0's
        # hand whenever the tracker owner sat anywhere else.
        self._viewer_pid = viewer_pid

        # Cards each player has played
        self._played_by: Dict[int, Set[int]] = {0: set(), 1: set(), 2: set(), 3: set()}
        # Pass history: what each player passed on
        self._pass_history: List[Tuple[int, Combo]] = []

        for a in log:
            if a.type == "play":
                for c in a.cards:
                    self._played_by[a.player].add(c.id)
            elif a.type == "pass" and a.passed_on:
                self._pass_history.append((a.player, a.passed_on))

        # Remaining hand sizes from the current state (updated externally)
        self._opp_sizes: Dict[int, int] = {p: 0 for p in range(4) if p != viewer_pid}

    def set_opponent_sizes(self, sizes: Dict[int, int]):
        self._opp_sizes.update(sizes)

    def played_count(self, player: int) -> int:
        """How many cards this player has tabled so far (public info)."""
        return len(self._played_by.get(player, ()))

    def pass_count(self, player: int) -> int:
        """How many times this player passed (public info)."""
        return sum(1 for p, _combo in self._pass_history if p == player)

    def unseen_cards(self) -> List[Card]:
        """All cards not in my hand and not played by anyone."""
        played_ids = set()
        for p in range(4):
            played_ids.update(self._played_by[p])
        result = []
        for i in range(108):
            if i not in self._my_card_ids and i not in played_ids:
                result.append(Card.from_id(i))
        return result

    def unseen_by_rank(self, rank: Rank) -> int:
        """How many cards of this rank are still unseen (not mine, not played)."""
        played = sum(1 for p in range(4) for cid in self._played_by[p]
                     if Card.from_id(cid).rank == rank)
        my_count = sum(1 for c in self._my_hand if c.rank == rank)
        total = 8 if rank.value <= 14 else (4 if rank == Rank.SMALL_JOKER else 4 if rank == Rank.BIG_JOKER else 0)
        return total - played - my_count

    def sample_opponent_hands(self) -> Dict[int, List[Card]]:
        """Randomly distribute unseen cards to opponents based on hand sizes.

        Honors pass constraints softly (stage 4.2): a player who PASSED on a
        single of effective rank R does not receive sample cards that would
        clearly beat that single (eff > R+1), when the constraint is
        feasible. Other combo types are too coarse for card-level filtering
        and are skipped. Falls back to unconstrained when the filtered pool
        cannot cover the hand size.
        """
        unseen = self.unseen_cards()
        random.shuffle(unseen)

        excluded: Dict[int, Set[int]] = {}
        for player, passed_on in self._pass_history:
            if passed_on is None or passed_on.combo_type != ComboType.SINGLE:
                continue
            r_eff = effective_rank(passed_on.main_rank, self._level)
            ids = {c.id for c in unseen
                   if c.rank.value <= 14
                   and effective_rank(c.rank, self._level) > r_eff + 1}
            if ids:
                excluded.setdefault(player, set()).update(ids)

        result: Dict[int, List[Card]] = {p: [] for p in range(4) if p != self._viewer_pid}
        pool = list(unseen)
        random.shuffle(pool)
        used: Set[int] = set()
        for pid in result:
            size = self._opp_sizes.get(pid, 0)
            ex = excluded.get(pid)
            if ex:
                allowed = [c for c in pool if c.id not in ex and c.id not in used]
                if len(allowed) >= size:
                    result[pid] = allowed[:size]
                    used.update(c.id for c in result[pid])
                    continue
                # constraint infeasible for this hand size → unconstrained
            take = [c for c in pool if c.id not in used][:size]
            result[pid] = take
            used.update(c.id for c in take)
        return result

    def prob_player_has_bomb(self, player: int) -> float:
        """Crude estimate: probability opponent has at least one bomb."""
        prob_no_bomb = 1.0
        for rank in Rank:
            if rank.value > 14:
                continue
            unseen = self.unseen_by_rank(rank)
            if unseen >= 4:
                # Hypergeometric-ish
                total_unseen = len(self.unseen_cards())
                hand_size = self._opp_sizes.get(player, 0)
                if total_unseen == 0 or hand_size == 0:
                    continue
                p_has = 1.0
                for k in range(4):
                    if unseen - k <= 0 or total_unseen - k <= 0:
                        p_has = 0
                        break
                    p_has *= (unseen - k) / (total_unseen - k)
                p_has = min(p_has * (hand_size / total_unseen) * 10, 0.9)
                prob_no_bomb *= (1 - p_has)
        return 1 - prob_no_bomb

    def prob_player_has_rank(self, player: int, rank: Rank, min_count: int = 1) -> float:
        """Hypergeometric probability that opponent has >= min_count cards of rank."""
        unseen_of_rank = self.unseen_by_rank(rank)
        total_unseen = len(self.unseen_cards())
        hand_size = self._opp_sizes.get(player, 0)
        if unseen_of_rank == 0 or hand_size == 0 or total_unseen == 0:
            return 0.0
        prob = 0.0
        for k in range(min_count, min(hand_size, unseen_of_rank) + 1):
            if total_unseen - unseen_of_rank >= hand_size - k:
                prob += (math.comb(unseen_of_rank, k) *
                         math.comb(total_unseen - unseen_of_rank, hand_size - k) /
                         math.comb(total_unseen, hand_size))
        return min(prob, 1.0)
