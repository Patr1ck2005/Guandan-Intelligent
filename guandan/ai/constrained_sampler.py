"""Constrained Sampler — uses pass history to improve unseen card distribution.

When a player passes on a combo, they likely don't have cards that beat it.
This sampler weights the random distribution to reflect that.
"""

from __future__ import annotations

import random
from typing import Dict, List, Tuple

from ..card import Card, Rank
from ..combo import Combo, ComboType
from .player_view import PlayerView
from .action_log import CardTracker


class ConstrainedSampler:
    """Samples opponent hands using pass-history constraints.

    Strategy:
      1. Identify which ranks each player "can't have" based on passes
      2. Deal unconstrained cards first (randomly)
      3. Deal constrained cards weighted away from the constrained player
    """

    name = "ConstrainedSampler"
    description = "约束采样：利用过牌历史优化 unseen 分布"

    def sample(self, view: PlayerView) -> Dict[int, Tuple[Card, ...]]:
        tracker = view.tracker
        unseen = self._unseen(view)
        # Opponents are everyone except the deciding seat. Hardcoding
        # [1, 2, 3] silently blanked seat 0 in every simulation once the
        # decider sat anywhere else (seat N treated itself as an opponent
        # and never sampled seat 0).
        opps = [p for p in range(4) if p != view.player_id]

        if tracker is None or len(tracker._pass_history) == 0:
            # No pass history — fall back to random
            return self._random_deal(unseen, view)

        # Build constraint map: for each player, which ranks they likely don't have
        constrained_ranks: Dict[int, set] = {p: set() for p in opps}

        for player, passed_on in tracker._pass_history:
            if player not in constrained_ranks:
                continue  # the viewer's own passes don't constrain opponents
            ct = passed_on.combo_type
            rank_val = passed_on.main_rank.value
            if ct in (ComboType.SINGLE, ComboType.PAIR, ComboType.TRIPLE):
                # Player passed on a combo of this rank → likely no higher of same type
                # Mark ranks higher than this as "unlikely"
                constrained_ranks[player].update(
                    r for r in range(rank_val + 1, 15)  # 2..14
                )
            if ct == ComboType.SINGLE:
                # Also: if they passed on a single K, they likely don't have ANY higher single
                # (they'd have played it to take control)
                pass

        # Split unseen cards into constrained and free
        free_cards: List[Card] = []
        constrained_cards: Dict[int, List[Card]] = {p: [] for p in opps}

        for c in unseen:
            constrained = False
            for p in opps:
                if c.rank.value in constrained_ranks.get(p, set()):
                    constrained_cards[p].append(c)
                    constrained = True
                    break
            if not constrained:
                free_cards.append(c)

        # Deal: distribute free cards randomly, constrained cards away from players
        sizes = {p: view.opponent_hand_size(p) for p in opps}
        result: Dict[int, Tuple[Card, ...]] = {p: () for p in opps}
        dealt: Dict[int, List[Card]] = {p: [] for p in opps}

        # 1. Deal free cards randomly to fill up
        random.shuffle(free_cards)
        idx = 0
        for p in opps:
            need = sizes[p]
            if need <= 0:
                continue
            take = min(need, len(free_cards) - idx)
            if take > 0:
                dealt[p] = list(free_cards[idx:idx + take])
            idx += need

        # 2. Deal constrained cards — for cards constrained against player P, deal to other players first
        # Add leftover constrained cards to the general pool
        leftover = []
        for p, cards in constrained_cards.items():
            random.shuffle(cards)
            # Give these cards to the OTHER opponents (not p)
            others = [q for q in opps if q != p]
            cp_idx = 0
            for q in others:
                remaining = sizes[q] - len(dealt.get(q, []))
                if remaining > 0 and cp_idx < len(cards):
                    take = min(remaining, len(cards) - cp_idx)
                    dealt.setdefault(q, []).extend(cards[cp_idx:cp_idx + take])
                    cp_idx += take
            leftover.extend(cards[cp_idx:])

        # 3. Fill remaining with leftover + extra unseen
        random.shuffle(leftover)
        for p in opps:
            remaining = sizes[p] - len(dealt.get(p, []))
            if remaining > 0 and leftover:
                take = min(remaining, len(leftover))
                dealt.setdefault(p, []).extend(leftover[:take])
                leftover = leftover[take:]

        # If still not enough, pad with random cards from unseen (shouldn't happen)
        all_dealt = sum(len(dealt.get(p, [])) for p in opps)
        if all_dealt < len(unseen):
            extra = [c for c in unseen
                     if not any(c in dealt.get(p, []) for p in opps)]
            random.shuffle(extra)
            ei = 0
            for p in opps:
                remaining = sizes[p] - len(dealt.get(p, []))
                if remaining > 0 and ei < len(extra):
                    dealt.setdefault(p, []).extend(extra[ei:ei + remaining])
                    ei += remaining

        for p in opps:
            result[p] = tuple(dealt.get(p, [])[:sizes[p]])

        return result

    def _unseen(self, view: PlayerView) -> List[Card]:
        my_ids = {c.id for c in view.my_hand}
        played_ids = {c.id for c in view.played_cards}
        return [Card.from_id(i) for i in range(108)
                if i not in my_ids and i not in played_ids]

    def _random_deal(self, unseen: List[Card], view: PlayerView) -> Dict[int, Tuple[Card, ...]]:
        pool = list(unseen)
        random.shuffle(pool)
        result = {}
        idx = 0
        for p in range(4):
            if p == view.player_id:
                continue
            size = view.opponent_hand_size(p)
            result[p] = tuple(pool[idx:idx + size])
            idx += size
        return result
