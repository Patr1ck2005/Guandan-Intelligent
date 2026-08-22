"""Prompt templates and game-state-to-text serialization."""

from __future__ import annotations

from typing import List, Tuple

from ...card import Card, Rank
from ...combo import Combo, ComboType

# ==================================================================
# System Prompt
# ==================================================================

SYSTEM_PROMPT = """You are an expert Guandan (掼蛋) player. You will receive the current game state and a numbered list of legal plays. Analyze the situation and choose the best play.

# Guandan — Complete Rules

## Deck and Setup
- 2 standard 54-card decks = 108 cards total (each card appears twice).
- 4 players, seated crosswise. P0 partners with P2, P1 partners with P3.
- Each player receives 27 cards at the start of a round.
- The game uses a "level" system. Rounds are played at a level (2 through A).
- The Heart-♥ card of the current level rank is the WILD CARD (万能牌/逢人配).

## Card Ranking (lowest to highest)
3 < 4 < 5 < 6 < 7 < 8 < 9 < 10 < J < Q < K < A < LEVEL_RANK < SMALL_JOKER < BIG_JOKER

The LEVEL_RANK is the current level's rank (e.g. if level=5, rank 5 sits between A and Small Joker).
Effective rank is what matters for comparisons, not face value.

## Wild Card (Heart of Level Rank)
- The Heart-♥ card of the current level rank is WILD. It can substitute for any rank from 3 through A.
- A wild card CANNOT substitute for a joker. It CANNOT be used as a 2.
- Multiple wild cards can be used together in the same combo.
- A wild card retains its original identity for display but takes on the needed rank for the combo.
- Wild cards are extremely valuable. Using one as a plain single wastes its substitution power entirely.

## Combo Types (Legal Plays)

### 1. SINGLE — 1 card
Any single card.

### 2. PAIR — 2 cards of the same effective rank
Wild cards can fill in for missing rank matches.

### 3. TRIPLE — 3 cards of the same effective rank
Also called "three-of-a-kind".

### 4. TRIPLE+PAIR — 3 cards of one rank + 2 cards of a different rank
The triple and the pair must be of DIFFERENT ranks. Also called "full house" or "三带二".

### 5. STRAIGHT — 5 or more cards of consecutive ranks
- Must have at least 5 cards. Ranks must be consecutive (e.g. 3-4-5-6-7).
- Only ranks 3 through A are allowed. 2, jokers, and level-rank-as-face-value cannot be in a straight.
- Wild cards CAN be used to fill gaps in a straight.
- A straight of length N+1 beats a straight of length N (same end rank), BUT straights of DIFFERENT lengths are DIFFERENT combo types — a 5-card straight CANNOT beat a 6-card straight.
  ACTUALLY: all straights are the SAME combo type (STRAIGHT). A longer straight with the same end rank does beat a shorter one. But straights of any length must match the SAME type for comparison — meaning you CAN beat a 5-card straight with a 5-card straight, or a 6-card straight with a 6-card straight. A longer straight of a given type is simply a higher-ranked play of that type.

CORRECTION: In Guandan, STRAIGHT is one combo type regardless of length. To beat a straight, you must play a straight of the SAME length with a HIGHER end rank. Bombs can beat any straight.

### 6. CONSECUTIVE PAIRS — 3 or more consecutive ranks, 2 cards each
- Minimum: 3 consecutive ranks x 2 cards = 6 cards (e.g. 3-3-4-4-5-5).
- Also called "连对" or "板凳". Ranks 3-A only, same as straights.
- Wild cards can fill in.

### 7. CONSECUTIVE TRIPLES — 2 or more consecutive ranks, 3 cards each
- Minimum: 2 consecutive ranks x 3 cards = 6 cards (e.g. 3-3-3-4-4-4).
- Also called "钢板" or "飞机". Ranks 3-A only.
- Can optionally include附加 (side cards): play 3+3 plus two singles or two pairs of DIFFERENT ranks from the triple ranks.
  NOTE: This variant is not universally played. In this implementation, consecutive triples are played WITHOUT side cards.

### 8. BOMB — 4 to 8 cards of the same effective rank
- The size (number of cards) determines bomb strength. A 5-card bomb beats a 4-card bomb regardless of rank.
- Between bombs of the same size: higher effective rank wins.

### 9. STRAIGHT FLUSH — 5 consecutive cards of the SAME suit
- Very powerful. Beats any non-bomb play and any same-size normal bomb.
- Must be exactly 5 cards. Same suit, consecutive ranks 3-A.

### 10. ROCKET — exactly 4 jokers: 2 Big Jokers + 2 Small Jokers
- The ultimate play. Beats absolutely everything. Cannot be beaten.
- Using it means you lose your strongest weapon for the rest of the round.

## Beat Rules — Detailed
- When following (not the leader): you must play a combo of the EXACT SAME TYPE and HIGHER RANK to beat the current table combo.
- EXCEPTION: Bombs (types 8, 9, 10) can beat ANY non-bomb combo, regardless of type.
- A larger bomb beats a smaller bomb. A same-size bomb with higher rank beats a lower-rank same-size bomb.
- A straight flush (9) beats a normal bomb (8) of the same size. 4-card normal bomb < 4-card straight flush? NO — straight flush beats any normal bomb AND any non-bomb.
- A rocket (10) beats everything including straight flushes.
- When comparing same-type combos: compare the MAIN RANK. For straights and consecutive combos, compare the ENDING (highest) rank.

## Trick Flow
1. The trick leader may play ANY valid combo from their hand.
2. Play proceeds clockwise. Each subsequent player must either:
   a) Play a HIGHER combo of the SAME TYPE (or a bomb), OR
   b) PASS.
3. The trick ends when 3 consecutive players pass (all other active players have passed since the last play).
4. The last player who played (NOT passed) wins the trick and leads the next trick.
5. A player who has emptied their hand is "finished" and is skipped in subsequent tricks.

## Round End and Scoring
- First player to empty their hand: 1st place (头游/touyou).
- Second: 2nd place, third: 3rd place, last: 4th place (末游/moyou).
- The round ends when 3 players have finished.
- If 1st and 2nd are partners: their team gets +3 levels.
- If 1st and 3rd are partners: their team gets +2 levels.
- If 1st and 4th are partners: their team gets +1 level.
- The levels apply to the winning team only. The losing team stays at their current level.
- First team to win a round while at level A (or above) wins the entire game.

## Partnership
- You are on a team with the player opposite you.
- Your partner's success is your success. You want your partner to finish early.
- If your partner played the current combo on the table, you may want to pass to let them keep control.
- If an opponent plays a strong combo, you may need to use a bomb to prevent them from winning the trick.

## Strategy Principles
- Card efficiency: playing more cards per turn empties your hand faster.
- Control: the player who wins a trick gets to lead the next one. Leading gives you initiative.
- Resource conservation: wild cards and jokers are limited. Bombs are limited. Don't waste them.
- Position matters: an opponent with few cards remaining is an immediate threat. Your partner with few cards is an asset.
- Passing is strategic: passing on a low play preserves your high cards for later. But passing too often lets opponents control the game.

Reply with ONLY a JSON object: {"choice": <number>}"""

# ==================================================================
# Combo type labels
# ==================================================================

COMBO_TYPE_LABEL: dict = {
    ComboType.SINGLE: "SINGLE",
    ComboType.PAIR: "PAIR",
    ComboType.TRIPLE: "TRIPLE",
    ComboType.TRIPLE_PAIR: "TRIPLE+PAIR",
    ComboType.STRAIGHT: "STRAIGHT",
    ComboType.CONSECUTIVE_PAIRS: "CONSECUTIVE_PAIRS",
    ComboType.CONSECUTIVE_TRIPLES: "CONSECUTIVE_TRIPLES",
    ComboType.NORMAL_BOMB: "BOMB",
    ComboType.STRAIGHT_FLUSH: "STRAIGHT_FLUSH",
    ComboType.ROCKET: "ROCKET",
}


# ==================================================================
# User message builder
# ==================================================================

def build_user_message(
    hand: Tuple[Card, ...],
    level: int,
    player_id: int,
    table_combo: Combo | None,
    table_last_player: int,
    trick_history: List[Tuple[int, Combo | None]],
    opponent_sizes: dict,
    finished: list,
    round_num: int,
    trick_num: int,
    candidates: List[Combo] | None = None,
    can_pass: bool = False,
) -> str:
    teammate = (player_id + 2) % 4
    total = len(hand)

    lines = [
        f"Level {level} (Heart-{level}=WILD) | Round {round_num} Trick {trick_num}",
        f"Your position: P{player_id} | Teammate: P{teammate}",
        f"Your hand: {total} cards remaining",
        "",
    ]

    # === Hand ===
    lines.append("## Your Hand")
    lines.append(_hand_display(hand, level))
    lines.append("")

    # === Hand analysis ===
    lines.append("## Hand Analysis")
    lines.append(_hand_analysis(hand, level, candidates))
    lines.append("")

    # === Table ===
    lines.append("## Table")
    if table_combo is None:
        lines.append("Empty. You are the LEADER — play any valid combo.")
    else:
        who = "teammate" if table_last_player == teammate else "opponent"
        lines.append(
            f"{COMBO_TYPE_LABEL.get(table_combo.combo_type, '?')} "
            f"{' '.join(c.display for c in table_combo.cards)} "
            f"(rank={table_combo.main_rank.name}, {table_combo.length} cards)"
        )
        lines.append(f"Played by P{table_last_player} ({who})")
    lines.append("")

    # === Opponents ===
    lines.append("## Opponents")
    threats = []
    for p in range(4):
        if p == player_id:
            continue
        role = "teammate" if p == teammate else "opp"
        sz = opponent_sizes.get(p, 0)
        lines.append(f"P{p} ({role}): {sz} cards")
        if role == "opp" and sz <= 10:
            threats.append(f"P{p}")
    if threats:
        lines.append(f"DANGER: {', '.join(threats)} is close to winning!")
    lines.append("")

    # === Trick history ===
    if trick_history:
        lines.append("## Trick History")
        for pid, combo in trick_history[-8:]:
            if combo is None:
                lines.append(f"P{pid} passed")
            else:
                cards_str = " ".join(c.display for c in combo.cards)
                lines.append(
                    f"P{pid}: {COMBO_TYPE_LABEL.get(combo.combo_type, '?')} [{cards_str}]"
                )
        lines.append("")

    # === Candidates ===
    if candidates is not None:
        lines.append("## Your Legal Plays")
        if can_pass:
            lines.append(f"0. PASS — keep all {total} cards, wait for better opportunity")
        for i, combo in enumerate(candidates):
            idx = i + 1 if can_pass else i
            cards_str = " ".join(c.display for c in combo.cards)
            remaining = total - combo.length
            wild_count = len(combo.wild_indices)
            tags = []
            if combo.is_bomb:
                tags.append("BOMB")
            if wild_count:
                wasteful = combo.combo_type in (ComboType.SINGLE, ComboType.PAIR)
                tags.append(f"WASTES {wild_count} WILD" if wasteful else f"uses {wild_count} wild")
            tag_str = f" [{', '.join(tags)}]" if tags else ""
            lines.append(
                f"{idx}. {COMBO_TYPE_LABEL.get(combo.combo_type, '?')} {cards_str} "
                f"| rank={combo.main_rank.name} | {combo.length} cards out, "
                f"{remaining} left{tag_str}"
            )
        lines.append("")
        if can_pass:
            lines.append("Choose carefully. Consider passing if no option is good.")
        else:
            lines.append("You MUST play. Pick the best option.")
        lines.append('Reply with ONLY: {"choice": <number>}')
    else:
        # Free mode
        lines.append("## Your Cards")
        lines.append("!!! WILD cards are precious — do NOT play them as singles !!!")
        lines.append("")
        for c in sorted(hand, key=lambda x: (x.rank.value, x.suit.value)):
            if c.is_wild(level):
                lines.append(f"  {c.id}: {c.display} ***WILD*** (any rank 3-A)")
            else:
                lines.append(f"  {c.id}: {c.display}")
        lines.append("")
        lines.append(
            'To PASS: {"play": "pass"}\n'
            'To play: {"play": [<card_id>, ...]}  // must form a valid combo'
        )

    return "\n".join(lines)


# ==================================================================
# Helpers
# ==================================================================

def _hand_display(hand: Tuple[Card, ...], level: int) -> str:
    """Group hand cards by rank for compact display."""
    by_rank: dict = {}
    for c in hand:
        by_rank.setdefault(c.rank, []).append(c)
    lines = []
    rank_order = sorted(by_rank.keys(), key=lambda r: r.value)
    for rank in rank_order:
        cards = by_rank[rank]
        card_strs = []
        for c in sorted(cards, key=lambda x: x.suit.value):
            mark = "(WILD)" if c.is_wild(level) else ""
            card_strs.append(f"{c.display}{mark}")
        lines.append(f"  {rank.name}: {' '.join(card_strs)}")
    return "\n".join(lines)


def _hand_analysis(
    hand: Tuple[Card, ...],
    level: int,
    candidates: List[Combo] | None,
) -> str:
    """Analyze hand composition to help the LLM reason strategically."""
    wilds = [c for c in hand if c.is_wild(level)]
    jokers = [c for c in hand if c.rank in (Rank.SMALL_JOKER, Rank.BIG_JOKER)]
    normals = [c for c in hand if not c.is_wild(level) and c.rank not in (Rank.SMALL_JOKER, Rank.BIG_JOKER)]

    lines = []
    if wilds:
        lines.append(f"Wild cards: {len(wilds)} ({', '.join(c.display for c in wilds)}) — extremely valuable, conserve them")

    if jokers:
        lines.append(f"Jokers: {len(jokers)} ({', '.join(c.display for c in jokers)})")

    if candidates:
        # Summarize candidate types available
        types_seen = set()
        bombs_available = 0
        large_combos = 0  # 4+ cards
        single_count = 0
        for c in candidates:
            types_seen.add(c.combo_type)
            if c.is_bomb:
                bombs_available += 1
            if c.length >= 4:
                large_combos += 1
            if c.combo_type == ComboType.SINGLE:
                single_count += 1

        type_names = sorted(COMBO_TYPE_LABEL.get(t, t.name) for t in types_seen)
        lines.append(f"Available play types: {', '.join(type_names)}")
        if large_combos:
            lines.append(f"Multi-card options (4+ cards): {large_combos} — clearing more cards per turn is efficient")
        if bombs_available:
            lines.append(f"Bombs available: {bombs_available} — use defensively, not as opener")
        if single_count:
            lines.append(f"Singles: {single_count} — only as last resort when no better option exists")
    else:
        lines.append(f"Normal cards: {len(normals)} | Wilds: {len(wilds)} | Jokers: {len(jokers)}")

    return "\n".join(lines)
