"""Test scenario library for the AI arena.

Each scenario defines a complete game state: all 4 players' hands,
the current table combo, and any already-played cards.
Undefined cards go to played_cards automatically by _build_view().

Card IDs 0-107: 2 decks × 54 cards.
  deck 0: 0-53 (52 normal + SJ + BJ)
  deck 1: 54-107 (52 normal + SJ + BJ)
Within each deck: suit = idx//13, rank = idx%13+2.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from guandan.card import Card


def _pick(rank_val: int, count: int, used: set, level: int = 2) -> List[int]:
    """Find `count` card IDs of given rank, skip wilds at the current level."""
    result = []
    for i in range(108):
        if i in used:
            continue
        c = Card.from_id(i)
        if c.rank.value == rank_val and not c.is_wild(level):
            result.append(i)
            used.add(i)
            if len(result) >= count:
                return result
    raise ValueError(f"Not enough cards for rank {rank_val} (need {count}, found {len(result)})")


def _picks(rank_vals: List[int], counts: List[int], used: set, level: int = 2) -> List[int]:
    """Pick cards of multiple ranks."""
    result = []
    for r, c in zip(rank_vals, counts):
        result.extend(_pick(r, c, used, level))
    return result


def _hand(ranks: List[int], counts: List[int], used: set, level: int = 2) -> List[int]:
    """Build a hand: pick specific counts of specific ranks."""
    return _picks(ranks, counts, used, level)


@dataclass
class Scenario:
    id: str
    name: str
    category: str
    description: str
    hand: List[int]                          # AI player's (P0) cards
    table: Optional[List[int]] = None        # combo on the table
    table_player: int = 1                    # who played the table combo
    level: int = 2
    opponents: Dict[int, List[int]] = field(default_factory=dict)  # P1/P2/P3 hands
    played_cards: List[int] = field(default_factory=list)
    expected_play: Optional[List[int]] = None
    expected_pass: bool = False
    reasoning: str = ""
    # Eval metadata (set by the eval module; curated scenarios default to these)
    source: str = "curated"       # curated | synthetic | consensus
    ground_truth: str = "manual"  # manual | exact | consensus
    difficulty: str = ""          # easy | medium | hard (set by grading)
    tags: List[str] = field(default_factory=list)


# ==================================================================
# Helper: build a scenario with all 4 hands defined
# ==================================================================
def _make_scenario(id: str, name: str, category: str, description: str,
                   hand_ranks: List[int], hand_counts: List[int],
                   opp1: tuple, opp2: tuple, opp3: tuple,
                   table_ranks: Optional[List[int]] = None,
                   table_counts: Optional[List[int]] = None,
                   table_player: int = 1, level: int = 2,
                   expected_play_ranks: Optional[List[int]] = None,
                   expected_play_counts: Optional[List[int]] = None,
                   expected_pass: bool = False,
                   reasoning: str = "") -> Scenario:
    """Build a complete scenario with all 4 hands.

    opp1/opp2/opp3 are (ranks, counts) tuples.
    Remaining (unreferenced) cards are automatically treated as played.
    """
    used = set()
    hand = _hand(hand_ranks, hand_counts, used, level)
    o1 = _hand(opp1[0], opp1[1], used, level)
    o2 = _hand(opp2[0], opp2[1], used, level)
    o3 = _hand(opp3[0], opp3[1], used, level)
    table = _hand(table_ranks, table_counts, used, level) if table_ranks else None
    expected = _hand(expected_play_ranks, expected_play_counts, used, level) if expected_play_ranks else None

    return Scenario(
        id=id, name=name, category=category, description=description,
        hand=hand, table=table, table_player=table_player, level=level,
        opponents={1: o1, 2: o2, 3: o3},
        expected_play=expected, expected_pass=expected_pass,
        reasoning=reasoning,
    )


# ==================================================================
# Category 1: Full-information deduction (完全信息)
# Tests strategic decisions where all hands are known.
# ==================================================================

DEDUCTION_SCENARIOS = [
    _make_scenario(
        id="deduce_pair_teammate",
        name="队友可接盘一对",
        category="deduction",
        description="桌面对5(右家出)。手牌对8对3+散牌。对家有对10。应出对8，对家可接盘。",
        hand_ranks=[8, 3, 6, 7], hand_counts=[2, 2, 1, 1],
        opp1=([4, 9, 10], [2, 1, 1]),
        opp2=([10, 11, 13, 14], [2, 2, 1, 1]),
        opp3=([12, 5, 6], [2, 1, 1]),
        table_ranks=[5], table_counts=[2], table_player=1,
        expected_play_ranks=[8], expected_play_counts=[2],
        reasoning="出对8，对家用对10接盘"
    ),
    _make_scenario(
        id="deduce_no_bomb_lead_bomb",
        name="三家无炸弹可首出炸弹",
        category="deduction",
        description="首家。手牌4张8炸弹+2散牌。三家均无炸弹。应首出炸弹抢控制。",
        hand_ranks=[8, 3, 4], hand_counts=[4, 1, 1],
        opp1=([10, 11, 12], [2, 2, 2]),
        opp2=([5, 6, 7], [2, 2, 2]),
        opp3=([9, 13, 14], [2, 2, 2]),
        table_ranks=None, table_player=1,
        expected_play_ranks=[8], expected_play_counts=[4],
        reasoning="三家无炸弹，首出炸弹抢绝对控制"
    ),
    _make_scenario(
        id="deduce_choose_between_combos",
        name="多牌型选择",
        category="deduction",
        description="桌面单5(右家出)。手牌有单8、对6、三张3。应出单8，保留对6和三张3做大牌。",
        hand_ranks=[8, 6, 3], hand_counts=[1, 2, 3],
        opp1=([4, 9], [1, 1]),
        opp2=([14, 11, 10, 9, 8, 7], [1, 1, 1, 1, 1, 1]),
        opp3=([13, 12, 5], [1, 1, 1]),
        table_ranks=[5], table_counts=[1], table_player=1,
        expected_play_ranks=[8], expected_play_counts=[1],
        reasoning="出单8压单5，保留对6和三张3做更大牌型"
    ),
    _make_scenario(
        id="deduce_bomb_chain",
        name="炸弹链判断",
        category="deduction",
        description="桌面4张5炸弹(右家出)。手牌4张8炸弹+散牌。三家无人有更大炸弹。应用8炸弹压。",
        hand_ranks=[8, 3, 6], hand_counts=[4, 1, 1],
        opp1=([9, 10, 11, 12], [2, 2, 2, 1]),
        opp2=([13, 14, 4, 7], [2, 2, 1, 1]),
        opp3=([5, 6, 7, 9], [2, 2, 1, 1]),
        table_ranks=[5], table_counts=[4], table_player=1,
        expected_play_ranks=[8], expected_play_counts=[4],
        reasoning="8炸弹压5炸弹，三家无更大炸弹"
    ),
    _make_scenario(
        id="deduce_triple_side_choice",
        name="三带二选择",
        category="deduction",
        description="桌面三带二(三张4+对5，右家出)。手牌三张8+对3+单6。应出三带二(三张8+对3)。",
        hand_ranks=[8, 3, 6], hand_counts=[3, 2, 1],
        opp1=([9, 10, 7], [2, 2, 1]),
        opp2=([14, 13, 12, 11, 5], [1, 1, 1, 1, 2]),
        opp3=([10, 9, 7, 6], [1, 2, 1, 1]),
        table_ranks=[4, 5], table_counts=[3, 2], table_player=1,
        expected_play_ranks=[8, 3], expected_play_counts=[3, 2],
        reasoning="三带二(三张8+对3)压桌面的三带二(三张4+对5)"
    ),
    # --- Merged from endgame ---
    _make_scenario(
        id="endgame_pair_vs_pair",
        name="残局：对子抉择",
        category="deduction",
        description="桌面对4(右家出)。手牌对8对3。应出对8压对4。",
        hand_ranks=[8, 3], hand_counts=[2, 2],
        opp1=([13, 12], [1, 1]),
        opp2=([14], [2]),
        opp3=([12, 11], [1, 1]),
        table_ranks=[4], table_counts=[2], table_player=1,
        expected_play_ranks=[8], expected_play_counts=[2],
        reasoning="对8压对4，保留对3"
    ),
    _make_scenario(
        id="endgame_bomb_take_control",
        name="残局：炸弹抢控制",
        category="deduction",
        description="桌面单K(右家出)。手牌4张8炸弹+单A。应出炸弹压K，单A收尾。",
        hand_ranks=[8, 14], hand_counts=[4, 1],
        opp1=([13, 5], [1, 1]),
        opp2=([12, 4], [1, 1]),
        opp3=([11, 6], [1, 1]),
        table_ranks=[13], table_counts=[1], table_player=1,
        expected_play_ranks=[8], expected_play_counts=[4],
        reasoning="炸弹压K后单A收尾走完"
    ),
    _make_scenario(
        id="endgame_exact_4cards",
        name="残局：4张牌首选",
        category="deduction",
        description="首家。手牌对A对3。应出对3，对A保留做大牌。",
        hand_ranks=[14, 3], hand_counts=[2, 2],
        opp1=([12, 4], [2, 2]),
        opp2=([13, 5], [2, 2]),
        opp3=([11, 6], [2, 1]),
        table_ranks=None, table_player=1,
        expected_play_ranks=[3], expected_play_counts=[2],
        reasoning="出对3，保留对A做大牌确保最后出牌权"
    ),
    _make_scenario(
        id="endgame_minimal_3cards",
        name="残局：最小3张牌",
        category="deduction",
        description="桌面单J(右家出)。手牌A+K+3。只剩3张，应出A赢K。",
        hand_ranks=[14, 13, 3], hand_counts=[1, 1, 1],
        opp1=([5, 4], [1, 1]),
        opp2=([12, 6], [1, 1]),
        opp3=([7, 8], [1, 1]),
        table_ranks=[11], table_counts=[1], table_player=1,
        expected_play_ranks=[14], expected_play_counts=[1],
        reasoning="出A压J保先手，剩K和3可依次出"
    ),
    _make_scenario(
        id="endgame_consecutive_lead",
        name="残局：连对开局",
        category="deduction",
        description="首家。手牌连对(3-4-5)+单A。应出连对，不应出单A。",
        hand_ranks=[14, 3, 4, 5], hand_counts=[1, 2, 2, 2],
        opp1=([13, 12, 11, 10, 9, 8, 7, 6], [1, 1, 1, 1, 1, 1, 1, 1]),
        opp2=([13, 12, 11, 10, 9, 8, 7, 6], [1, 1, 1, 1, 1, 1, 1, 1]),
        opp3=([13, 12, 11, 10, 9, 8, 7, 6], [1, 1, 1, 1, 1, 1, 1, 1]),
        table_ranks=None, table_player=1,
        expected_play_ranks=[3], expected_play_counts=[6],
        reasoning="出连对(334455)一次出6张，留单A收尾"
    ),
    _make_scenario(
        id="rank_card_single_2_level_2",
        name="级牌：单2应过牌",
        category="deduction",
        description="等级2。桌面单2(右家出)。手牌JA无王牌无炸弹。2是级牌(eff=15)，J和A都打不过，应过牌。",
        hand_ranks=[14, 11], hand_counts=[1, 1],
        opp1=([12, 9], [1, 1]),
        opp2=([13, 8], [1, 1]),
        opp3=([4, 3], [1, 1]),
        table_ranks=[2], table_counts=[1], table_player=1,
        expected_play_ranks=None, expected_play_counts=None,
        expected_pass=True,
        reasoning="级牌2(eff=15)>A(eff=14)>J(eff=11)，无牌可压，应过牌"
    ),
    _make_scenario(
        id="rank_card_single_2_level_3",
        name="级牌：等级3时2非级牌",
        category="deduction",
        description="等级3。桌面单2(右家出)。手牌J。2非级牌(eff=2)，J(eff=11)可压，应出J。",
        hand_ranks=[11], hand_counts=[1],
        opp1=([14, 6], [1, 1]),
        opp2=([13, 5], [1, 1]),
        opp3=([12, 4], [1, 1]),
        table_ranks=[2], table_counts=[1], table_player=1, level=3,
        expected_play_ranks=[11], expected_play_counts=[1],
        reasoning="等级3时2仅为普通牌(eff=2)，J(eff=11)可压"
    ),
]

# ==================================================================
# Category 2: Endgame exact solve (残局) — reserved for future
# ==================================================================

ENDGAME_SCENARIOS = []

# ==================================================================
# Category 3: Probabilistic sampling (不确定采样)
# ==================================================================

SAMPLING_SCENARIOS = []

# ==================================================================
# Category 4: Opening evaluation (开局评估)
# ==================================================================

OPENING_SCENARIOS = []

ALL_SCENARIOS = DEDUCTION_SCENARIOS + SAMPLING_SCENARIOS + ENDGAME_SCENARIOS + OPENING_SCENARIOS


def get_scenario_by_id(scenario_id: str) -> Optional[Scenario]:
    for s in ALL_SCENARIOS:
        if s.id == scenario_id:
            return s
    return None


def get_scenarios_by_category(category: str) -> List[Scenario]:
    return [s for s in ALL_SCENARIOS if s.category == category]


def list_categories() -> List[str]:
    # Only categories that actually have scenarios. The fixed 4-name list
    # advertised sampling/endgame/opening with zero data behind them — the
    # T-tab scene picker rendered "无场景" and its benchmark always showed
    # "无结果" (the category existed in name only).
    return sorted({s.category for s in ALL_SCENARIOS})
