"""Arena API — scenario analysis, model comparison, and benchmarking.

Blueprint registered on the Flask app.
"""

import time
from flask import Blueprint, jsonify, request

from ...card import Card
from ...combo_parser import ComboParser
from ...game_state import GameState
from ...rules import RulesEngine
from ...table import TableState
from ...ai.player_view import PlayerView
from ...state_utils import next_active_player
from ...ai.registry import get_schema, create_agent_for_player
from ...logging import game_logger
from ...arena.scenarios import ALL_SCENARIOS, get_scenario_by_id, list_categories
from ...eval.common import (
    _card_json, _cards_json, _rank_signature, _build_view, _make_arena_agent,
)

arena_bp = Blueprint('arena_api', __name__)


# (card serialization / scenario view / agent construction moved to
#  guandan.eval.common and imported above)


# ==================================================================
# Simulator — step-by-step game simulation from a scenario
# ==================================================================

import copy

class Simulator:
    """Drives step-by-step game simulation. Stores GameState + RulesEngine."""

    def __init__(self, scenario):
        self.level = scenario.level
        self.rules = RulesEngine(scenario.level)
        self._acc_trick_history = []

        # Build hands from scenario
        hands_dict = {0: tuple(Card.from_id(i) for i in scenario.hand)}
        for p, ids in scenario.opponents.items():
            hands_dict[int(p)] = tuple(Card.from_id(i) for i in ids)
        for p in range(4):
            if p not in hands_dict:
                hands_dict[p] = tuple()
        hands_tuple = tuple(hands_dict[p] for p in range(4))

        # Current player: if table exists and has a combo, next player after table_player
        # otherwise current_player from scenario or 0
        current = getattr(scenario, 'current_player', None)
        if current is None:
            if scenario.table:
                current = (scenario.table_player + 1) % 4
            else:
                current = 0

        # Build table
        trick_leader = scenario.table_player if scenario.table else current
        table = TableState(trick_leader=trick_leader, pass_count=0)
        if scenario.table:
            parser = ComboParser(scenario.level)
            combo = parser.parse([Card.from_id(i) for i in scenario.table])
            if combo:
                table.record_play(scenario.table_player, combo)

        # Build played_cards — add all unused cards
        assigned = set(scenario.hand)
        for ids in scenario.opponents.values():
            assigned.update(ids)
        assigned.update(scenario.table or [])
        assigned.update(scenario.played_cards)
        unused = [i for i in range(108) if i not in assigned]
        played_ids = list(scenario.played_cards) + unused
        played_cards = [Card.from_id(i) for i in played_ids]

        # Determine finished positions
        finished = [p for p in range(4) if len(hands_tuple[p]) == 0]

        self.state = GameState(
            level=scenario.level, round_number=1,
            hands=hands_tuple, current_player=current,
            table=table, trick_number=1,
            played_cards=played_cards,
            finished_positions=finished,
        )

    def apply_play(self, player_id: int, card_ids: list) -> str:
        """Validate and apply a play. Returns error string or empty on success."""
        hand = self.state.hands[player_id]
        cards = [c for c in hand if c.id in card_ids]

        result = self.rules.validate_play(
            cards=cards, hand=hand, table_state=self.state.table,
            player_id=player_id, finished_positions=self.state.finished_positions,
        )
        if not result.is_legal:
            return f"不合法出牌: {result.reason.name}"

        # Apply play
        played_set = {c.id for c in cards}
        new_hand = tuple(c for c in hand if c.id not in played_set)
        self.state.hands = tuple(
            new_hand if i == player_id else h for i, h in enumerate(self.state.hands)
        )
        self.state.played_cards.extend(cards)
        self.state.table.record_play(player_id, result.resolved_combo)
        self.state.current_player = (player_id + 1) % 4
        if not new_hand:
            self.state.finished_positions.append(player_id)

        self._check_trick_end()
        return ""

    def apply_pass(self, player_id: int) -> str:
        """Apply a pass. Returns error string or empty on success."""
        if not self.rules.can_pass(self.state.table, player_id):
            return "你是首家，不能过"
        self.state.table.record_pass(player_id)
        self.state.current_player = (player_id + 1) % 4
        self._check_trick_end()
        return ""

    @classmethod
    def from_custom(cls, hands: dict, table: list, table_player: int,
                    current_player: int, level: int = 2):
        """Factory: build a Simulator from raw hand assignments.

        Args:
            hands: {0: [card_ids], 1: [card_ids], 2: [card_ids], 3: [card_ids]}
            table: list of card_ids on the table (optional, empty list for none)
            table_player: who played the table combo (used as trick_leader)
            current_player: who should play next
            level: game level (2-14)
        """
        sim = cls.__new__(cls)
        sim.level = level
        sim.rules = RulesEngine(level)
        sim._acc_trick_history = []

        hands_tuple = tuple(
            tuple(Card.from_id(i) for i in hands.get(p, [])) for p in range(4)
        )

        # Build table
        trick_leader = table_player if table else current_player
        table_state = TableState(trick_leader=trick_leader, pass_count=0)
        if table:
            parser = ComboParser(level)
            combo = parser.parse([Card.from_id(i) for i in table])
            if combo:
                table_state.record_play(table_player, combo)

        # All cards not in any hand or on table → played
        assigned = set()
        for p in range(4):
            assigned.update(hands.get(p, []))
        assigned.update(table)
        unused = [i for i in range(108) if i not in assigned]
        played_cards = [Card.from_id(i) for i in unused]

        finished = [p for p in range(4) if len(hands_tuple[p]) == 0]

        sim.state = GameState(
            level=level, round_number=1,
            hands=hands_tuple, current_player=current_player,
            table=table_state, trick_number=1,
            played_cards=played_cards,
            finished_positions=finished,
        )
        return sim

    def _check_trick_end(self):
        """If trick ended, start a new trick."""
        table = self.state.table
        if table.last_played_player < 0:
            return
        other_active = [p for p in self.state.active_players if p != table.last_played_player]
        if table.pass_count >= len(other_active):
            # Trick ended — save history and reset
            self._acc_trick_history.extend(table.trick_history)
            leader = next_active_player(self.state, table.last_played_player)
            table.reset_for_new_trick(leader)
            self.state.trick_number += 1
            self.state.current_player = leader

    def get_state(self) -> dict:
        """Serialize complete game state for frontend."""
        s = self.state
        player_names = ["你", "右家", "对家", "左家"]

        # Players info
        players = []
        for p in range(4):
            is_finished = p in s.finished_positions
            players.append({
                "id": p, "name": player_names[p],
                "hand_size": 0 if is_finished else len(s.hands[p]),
                "hand": [] if is_finished else [self._card_json(c) for c in s.hands[p]],
                "finished": is_finished,
                "is_current": p == s.current_player,
            })

        # Table combo
        table_combo = None
        if s.table.current_combo is not None:
            tc = s.table.current_combo
            table_combo = {
                "type": tc.combo_type.name,
                "type_cn": _COMBO_TYPE_CN.get(tc.combo_type.value, tc.combo_type.name),
                "cards": [self._card_json(c) for c in tc.cards],
                "length": tc.length,
                "last_player": s.table.last_played_player,
            }

        # Full trick history (accumulated + current)
        trick_history = []
        all_entries = self._acc_trick_history + list(s.table.trick_history)
        for pid, combo in all_entries:
            if combo is None:
                trick_history.append({"player": pid, "pass": True})
            else:
                trick_history.append({
                    "player": pid, "pass": False,
                    "combo": {"type": combo.combo_type.name,
                              "type_cn": _COMBO_TYPE_CN.get(combo.combo_type.value, combo.combo_type.name),
                              "cards": [self._card_json(c) for c in combo.cards],
                              "length": combo.length}
                })

        can_pass = self.rules.can_pass(s.table, s.current_player)
        round_over = len(s.finished_positions) >= 3

        return {
            "players": players,
            "table_combo": table_combo,
            "trick_history": trick_history,
            "current_player": s.current_player,
            "trick_number": s.trick_number,
            "can_pass": can_pass,
            "round_over": round_over,
            "level": s.level,
            "finished_positions": [{"id": pid, "name": player_names[pid]} for pid in s.finished_positions],
            "trick_leader": s.table.trick_leader,
            "pass_count": s.table.pass_count,
        }

    def _card_json(self, card: Card) -> dict:
        suit_names = {0: "C", 1: "D", 2: "H", 3: "S", 4: ""}
        rank_names = {2:"2",3:"3",4:"4",5:"5",6:"6",7:"7",8:"8",9:"9",10:"10",
                      11:"J",12:"Q",13:"K",14:"A",15:"SJ",16:"BJ"}
        return {
            "id": card.id, "rank": card.rank.value,
            "rank_name": rank_names.get(card.rank.value, "?"),
            "suit": card.suit.value,
            "suit_name": suit_names.get(card.suit.value, ""),
            "display": card.display,
            "is_wild": card.is_wild(self.level),
            "is_joker": card.is_joker,
        }


# In-memory simulator storage (keyed by session-like ID)
# {sim_id: (Simulator, last_access_timestamp)}
_SIM_TTL = 10 * 60  # 10 minutes
_simulators: dict = {}


def _cleanup_sims():
    now = time.time()
    expired = [sid for sid, (_, ts) in _simulators.items() if now - ts > _SIM_TTL]
    for sid in expired:
        del _simulators[sid]


def _get_sim(sim_id: str) -> Simulator | None:
    _cleanup_sims()
    entry = _simulators.get(sim_id)
    if entry:
        sim, _ = entry
        _simulators[sim_id] = (sim, time.time())
        return sim
    return None


# (_make_arena_agent moved to guandan.eval.common and imported above)


def _serialize_candidate(c: "CandidateResult", level: int) -> dict:
    """Serialize a CandidateResult to JSON-safe dict."""
    d = {
        "combo_type": c.combo_type,
        "cards": c.cards,
        "card_ids": c.card_ids,
        "score": c.score,
        "win_rate": c.win_rate,
        "reasoning": c.reasoning,
    }
    if c.detail:
        d["detail"] = c.detail
    return d


def _serialize_analyze_result(result: "AnalyzeResult", level: int, model_id: str, model_name: str, elapsed_ms: float) -> dict:
    """Serialize an AnalyzeResult to JSON-safe dict."""
    choice_dict = None
    if result.choice:
        choice_dict = {
            "combo_type": result.choice.combo_type,
            "cards": result.choice.cards,
            "card_ids": result.choice.card_ids,
            "win_rate": result.choice.win_rate,
            "score": result.choice.score,
            "reasoning": result.choice.reasoning,
        }

    return {
        "model_id": model_id,
        "model_name": model_name,
        "choice": choice_dict,
        "pass_chosen": result.pass_chosen,
        "candidates": [_serialize_candidate(c, level) for c in (result.candidates or [])],
        "metrics": {
            "elapsed_ms": result.metrics.get("elapsed_ms", elapsed_ms),
            "timed_out": result.metrics.get("timed_out", False),
            "iterations": result.metrics.get("iterations", 0),
        },
    }


# ==================================================================
# Endpoints
# ==================================================================

@arena_bp.route("/api/arena/models")
def list_models():
    schema = get_schema()
    decider = schema.get("decider", {}).get("models", {})
    inner = schema.get("inner_model", {}).get("models", {})
    enumerator = schema.get("enumerator", {}).get("models", {})
    return jsonify({
        "outer": [{"id": mid, "name": m["name"], "description": m.get("description", ""),
                   "is_mc": m.get("is_mc", False)} for mid, m in decider.items()],
        "inner": [{"id": mid, "name": m["name"], "description": m.get("description", "")}
                  for mid, m in inner.items()],
        "enumerator": [{"id": mid, "name": m["name"], "description": m.get("description", "")}
                       for mid, m in enumerator.items()],
    })


@arena_bp.route("/api/arena/scenarios")
def list_scenarios():
    cat = request.args.get("category")
    scenarios = ALL_SCENARIOS
    if cat:
        scenarios = [s for s in scenarios if s.category == cat]
    return jsonify({
        "scenarios": [{"id": s.id, "name": s.name, "category": s.category,
                       "description": s.description, "hand_size": len(s.hand),
                       "has_table": s.table is not None,
                       "played_count": len(s.played_cards)} for s in scenarios],
        "categories": list_categories(),
    })


@arena_bp.route("/api/arena/scenarios/<scenario_id>")
def get_scenario(scenario_id):
    s = get_scenario_by_id(scenario_id)
    if not s:
        return jsonify({"error": "Not found"}), 404
    return jsonify({
        "id": s.id, "name": s.name, "category": s.category,
        "description": s.description, "level": s.level,
        "hand": _cards_json(s.hand, s.level),
        "table": _cards_json(s.table or [], s.level),
        "table_player": s.table_player,
        "played_cards": _cards_json(s.played_cards, s.level),
        "opponents": {str(p): _cards_json(ids, s.level) for p, ids in s.opponents.items()},
        "expected_play": s.expected_play,
        "expected_pass": s.expected_pass,
        "reasoning": s.reasoning,
    })


@arena_bp.route("/api/arena/scenarios/<scenario_id>/perspectives")
def get_perspectives(scenario_id):
    """Return the PlayerView for each of the 4 player perspectives."""
    s = get_scenario_by_id(scenario_id)
    if not s:
        return jsonify({"error": "Not found"}), 404

    perspectives = {}
    player_names = ["你", "AI-右家", "AI-对家", "AI-左家"]
    for p in range(4):
        view = _build_view(s, perspective=p)
        pj = view.to_json()
        pj["player_name"] = player_names[p]
        pj["my_hand"] = [_card_json(c, s.level) for c in view.my_hand] if p == 0 else []
        pj["my_hand_size"] = len(view.my_hand) if p == 0 else view.opponent_hand_size(p)
        perspectives[str(p)] = pj

    return jsonify({
        "scenario_id": s.id,
        "level": s.level,
        "perspectives": perspectives,
    })


@arena_bp.route("/api/arena/analyze/scenario", methods=["POST"])
def analyze_scenario():
    """Run models on a scenario and return full results with candidates.

    Request: {scenario_id, models: [{id, label, num_samples, ...}], perspective: 0}
    Response: {scenario, perspective, results: [{candidates, choice, metrics}]}
    """
    data = request.get_json() or {}
    scenario_id = data.get("scenario_id")
    model_profiles = data.get("models", [{"id": "blind"}])
    perspective = int(data.get("perspective", 0))
    debug = data.get("debug", False)

    scenario = get_scenario_by_id(scenario_id) if scenario_id else None
    if not scenario:
        return jsonify({"error": "Scenario not found"}), 404

    view = _build_view(scenario, perspective=perspective)
    schema = get_schema()
    schema_names = {}
    for cat in ["decider", "inner_model"]:
        for mid, m in schema.get(cat, {}).get("models", {}).items():
            schema_names[mid] = m["name"]

    # Clear AI log before running
    game_logger.clear()

    results = []
    for entry in model_profiles:
        mid = entry.get("id", entry) if isinstance(entry, dict) else entry
        label = entry.get("label") if isinstance(entry, dict) else None
        agent, name = _make_arena_agent(entry if isinstance(entry, dict) else {"id": mid})
        if agent is None:
            results.append({"model_id": mid, "error": "Unknown model"})
            continue
        name = label or schema_names.get(mid, name or mid)

        t0 = time.time()
        try:
            result = agent.analyze(view)
        except Exception as e:
            results.append({"model_id": mid, "model_name": name, "error": str(e)})
            continue
        elapsed = (time.time() - t0) * 1000

        r = _serialize_analyze_result(result, scenario.level, mid, name, elapsed)

        # Attach debug info if requested
        if debug:
            log_entries = game_logger.query(count=50)
            r["debug"] = {"ai_log": log_entries}
            # If ISMCTS, serialize root node tree
            if hasattr(agent, 'root') and agent.root is not None:
                r["debug"]["mcts_tree"] = agent.root.to_json()

        results.append(r)

    return jsonify({
        "scenario": {
            "id": scenario.id, "name": scenario.name, "category": scenario.category,
            "description": scenario.description, "level": scenario.level,
            "hand": _cards_json(scenario.hand, scenario.level),
            "table": _cards_json(scenario.table or [], scenario.level),
            "table_player": scenario.table_player,
            "played_cards": _cards_json(scenario.played_cards, scenario.level),
            "expected_play": scenario.expected_play,
            "expected_pass": scenario.expected_pass,
            "reasoning": scenario.reasoning,
        },
        "perspective": perspective,
        "results": results,
    })


@arena_bp.route("/api/arena/benchmark", methods=["POST"])
def benchmark():
    """Run models across a category of scenarios."""
    data = request.get_json() or {}
    model_profiles = data.get("models", [{"id": "blind"}])
    category = data.get("category")
    scenarios = ALL_SCENARIOS
    if category:
        scenarios = [s for s in scenarios if s.category == category]

    schema = get_schema()
    schema_names = {}
    for cat in ["decider", "inner_model"]:
        for mid, m in schema.get(cat, {}).get("models", {}).items():
            schema_names[mid] = m["name"]

    results = []
    for scenario in scenarios:
        view = _build_view(scenario, perspective=0)
        for entry in model_profiles:
            mid = entry.get("id", entry) if isinstance(entry, dict) else entry
            label = entry.get("label") if isinstance(entry, dict) else None
            agent, name = _make_arena_agent(entry if isinstance(entry, dict) else {"id": mid})
            if agent is None:
                continue
            name = label or schema_names.get(mid, name or mid)

            t0 = time.time()
            try:
                result = agent.analyze(view)
            except Exception as e:
                results.append({"scenario_id": scenario.id, "scenario_name": scenario.name,
                                "category": scenario.category, "model": mid, "model_name": name,
                                "error": str(e), "correct": False, "elapsed_ms": 0})
                continue
            elapsed = (time.time() - t0) * 1000

            correct = False
            if scenario.expected_pass:
                correct = result.pass_chosen
            elif scenario.expected_play and result.choice:
                correct = _rank_signature(result.choice.card_ids) == _rank_signature(scenario.expected_play)

            results.append({
                "scenario_id": scenario.id, "scenario_name": scenario.name,
                "category": scenario.category, "model": mid, "model_name": name,
                "choice_type": result.choice.combo_type if result.choice else "NONE",
                "chosen_cards": result.choice.cards if result.choice else [],
                "pass_chosen": result.pass_chosen, "correct": correct,
                "elapsed_ms": result.metrics.get("elapsed_ms", elapsed),
                "timed_out": result.metrics.get("timed_out", False),
            })

    # Build summary
    by_model = {}
    for r in results:
        mid = r["model"]
        if mid not in by_model:
            by_model[mid] = {"total": 0, "correct": 0, "total_ms": 0, "name": r.get("model_name", mid)}
        by_model[mid]["total"] += 1
        if r["correct"]:
            by_model[mid]["correct"] += 1
        by_model[mid]["total_ms"] += r["elapsed_ms"]

    summary = []
    for mid, s in by_model.items():
        summary.append({
            "model": mid, "model_name": s["name"],
            "accuracy": round(s["correct"] / s["total"] * 100, 1) if s["total"] > 0 else 0,
            "avg_ms": round(s["total_ms"] / s["total"], 1) if s["total"] > 0 else 0,
            "correct": s["correct"], "total": s["total"],
        })
    summary.sort(key=lambda x: x["accuracy"], reverse=True)
    return jsonify({"results": results, "summary": summary})


# ==================================================================
# Simulation API (step-by-step game replay)
# ==================================================================

import uuid

@arena_bp.route("/api/arena/sim/init", methods=["POST"])
def sim_init():
    """Create a new simulator from a scenario. Returns initial game state."""
    data = request.get_json() or {}
    scenario_id = data.get("scenario_id")
    scenario = get_scenario_by_id(scenario_id) if scenario_id else None
    if not scenario:
        return jsonify({"error": "Scenario not found"}), 404

    sid = uuid.uuid4().hex
    sim = Simulator(scenario)
    _simulators[sid] = (sim, time.time())
    state = sim.get_state()
    state["sim_id"] = sid
    return jsonify(state)


@arena_bp.route("/api/arena/sim/init_custom", methods=["POST"])
def sim_init_custom():
    """Create a simulator from a custom card assignment.

    Request:
    {
        hands: {"0": [card_ids], "1": [card_ids], "2": [card_ids], "3": [card_ids]},
        table: [card_ids] | [],
        table_player: 2,
        current_player: 0,
        level: 2
    }
    """
    data = request.get_json() or {}
    hands_raw = data.get("hands", {})
    table = data.get("table") or []
    table_player = data.get("table_player", 0)
    current_player = data.get("current_player", 0)
    level = data.get("level", 2)

    # Validate and normalize hands
    hands = {}
    all_assigned = set()
    for p in range(4):
        ids = hands_raw.get(str(p), [])
        if not isinstance(ids, list):
            return jsonify({"error": f"hands.{p} must be a list of card IDs"}), 400
        for cid in ids:
            if not (0 <= cid <= 107):
                return jsonify({"error": f"Invalid card ID {cid}"}), 400
            if cid in all_assigned:
                return jsonify({"error": f"Duplicate card ID {cid}"}), 400
            all_assigned.add(cid)
        hands[p] = ids

    # Validate table
    for cid in table:
        if not (0 <= cid <= 107):
            return jsonify({"error": f"Invalid table card ID {cid}"}), 400
        if cid in all_assigned:
            return jsonify({"error": f"Table card {cid} already assigned to a player"}), 400
        all_assigned.add(cid)

    # Validate level
    if not (2 <= level <= 14):
        return jsonify({"error": "Level must be 2-14"}), 400

    # Validate table_player and current_player
    if not (0 <= table_player <= 3):
        return jsonify({"error": "table_player must be 0-3"}), 400
    if not (0 <= current_player <= 3):
        return jsonify({"error": "current_player must be 0-3"}), 400

    sid = uuid.uuid4().hex
    try:
        sim = Simulator.from_custom(hands, table, table_player, current_player, level)
    except Exception as e:
        return jsonify({"error": f"Failed to create simulator: {e}"}), 500

    _simulators[sid] = (sim, time.time())
    state = sim.get_state()
    state["sim_id"] = sid
    return jsonify(state)


@arena_bp.route("/api/arena/sim/state", methods=["POST"])
def sim_state():
    """Get current simulator state."""
    data = request.get_json() or {}
    sim = _get_sim(data.get("sim_id"))
    if not sim:
        return jsonify({"error": "Simulator not found"}), 404
    state = sim.get_state()
    state["sim_id"] = data["sim_id"]
    return jsonify(state)


@arena_bp.route("/api/arena/sim/step", methods=["POST"])
def sim_step():
    """Execute a play or pass, advance to next player."""
    data = request.get_json() or {}
    sim = _get_sim(data.get("sim_id"))
    if not sim:
        return jsonify({"error": "Simulator not found"}), 404

    player_id = data.get("player_id", sim.state.current_player)
    if data.get("pass"):
        err = sim.apply_pass(player_id)
        if err:
            return jsonify({"error": err})
    else:
        card_ids = data.get("card_ids", [])
        if not card_ids:
            return jsonify({"error": "No card_ids provided"})
        err = sim.apply_play(player_id, card_ids)
        if err:
            return jsonify({"error": err})

    state = sim.get_state()
    state["sim_id"] = data["sim_id"]
    return jsonify(state)


@arena_bp.route("/api/arena/sim/analyze", methods=["POST"])
def sim_analyze():
    """Run model analysis on the current simulation state."""
    data = request.get_json() or {}
    sim = _get_sim(data.get("sim_id"))
    if not sim:
        return jsonify({"error": "Simulator not found"}), 404

    model_profiles = data.get("models", [{"id": "blind"}])
    state = sim.state
    level = state.level
    current_pid = state.current_player

    # Build PlayerView for the current player
    from ...ai.action_log import ActionLog
    action_log = ActionLog()
    view = PlayerView(state, current_pid, action_log)

    # Run models
    schema = get_schema()
    schema_names = {}
    for cat in ["decider", "inner_model"]:
        for mid, m in schema.get(cat, {}).get("models", {}).items():
            schema_names[mid] = m["name"]

    game_logger.clear()
    results = []

    for entry in model_profiles:
        mid = entry.get("id", entry) if isinstance(entry, dict) else entry
        label = entry.get("label") if isinstance(entry, dict) else None
        agent, name = _make_arena_agent(entry if isinstance(entry, dict) else {"id": mid})
        if agent is None:
            results.append({"model_id": mid, "error": "Unknown model"})
            continue
        name = label or schema_names.get(mid, name or mid)

        t0 = time.time()
        try:
            result = agent.analyze(view)
        except Exception as e:
            results.append({"model_id": mid, "model_name": name, "error": str(e)})
            continue
        elapsed = (time.time() - t0) * 1000

        r = _serialize_analyze_result(result, level, mid, name, elapsed)
        if data.get("debug"):
            log_entries = game_logger.query(count=50)
            r["debug"] = {"ai_log": log_entries}
        results.append(r)

    return jsonify({
        "current_player": current_pid,
        "results": results,
    })


@arena_bp.route("/api/arena/check_combo", methods=["POST"])
def check_combo():
    """Validate whether a set of cards forms a legal combo."""
    data = request.get_json() or {}
    card_ids = data.get("card_ids", [])
    level = data.get("level", 2)

    if not card_ids:
        return jsonify({"valid": True, "combo_type": None, "error": ""})

    try:
        cards = [Card.from_id(i) for i in card_ids]
    except Exception:
        return jsonify({"valid": False, "combo_type": None, "error": "Invalid card ID"})

    parser = ComboParser(level)
    combo = parser.parse(cards)
    if combo:
        return jsonify({
            "valid": True,
            "combo_type": _COMBO_TYPE_CN.get(combo.combo_type.value, combo.combo_type.name),
            "error": "",
        })
    else:
        return jsonify({
            "valid": False,
            "combo_type": None,
            "error": "无法识别的牌型组合",
        })


_COMBO_TYPE_CN = {
    1: "单张", 2: "对子", 3: "三条", 4: "三带二",
    5: "顺子", 6: "连对", 7: "钢板", 8: "炸弹", 9: "同花顺", 10: "天王炸",
}
