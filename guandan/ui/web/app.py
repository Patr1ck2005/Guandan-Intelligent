"""Flask web server for Guandan.

Routes:
  /           Main menu
  /game       4-player game (human vs 3 AI)
  /arena      AI test arena (endgame scenarios)
  /api/*      Game API
  /api/arena  Arena API

Run: python -m guandan.ui.web.app
"""

import json
import os
import threading
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()  # load DEEPSEEK_API_KEY from .env before any LLM imports

from flask import Flask, jsonify, render_template, request, session

from ..interactive import InteractiveGame
from .arena_api import arena_bp
from ...eval.eval_api import eval_bp

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent.parent / "config.json"

app = Flask(__name__)
app.jinja_env.auto_reload = True
app.secret_key = os.environ.get("GUANDAN_SECRET", os.urandom(24))
app.register_blueprint(arena_bp)
app.register_blueprint(eval_bp)

_games: dict = {}  # {sid: (InteractiveGame, last_access_timestamp)}
_GAME_TTL = 30 * 60  # 30 minutes
_GAMES_LOCK = threading.Lock()  # iterate-then-delete cleanup raced inserts (RuntimeError → 500)


def _unwrap_game(entry):
    """Handle legacy (plain object) and new (tuple) formats."""
    if isinstance(entry, tuple):
        return entry[0]
    return entry  # legacy: bare InteractiveGame


def _cleanup_games_locked():
    """Remove expired game sessions. Caller must hold _GAMES_LOCK."""
    now = time.time()
    expired = []
    for sid, entry in _games.items():
        ts = entry[1] if isinstance(entry, tuple) else 0
        if now - ts > _GAME_TTL:
            expired.append(sid)
    for sid in expired:
        del _games[sid]


def _get_game() -> InteractiveGame:
    sid = session.get("game_id")
    with _GAMES_LOCK:
        _cleanup_games_locked()
        if sid and sid in _games:
            game = _unwrap_game(_games[sid])
            _games[sid] = (game, time.time())
            return game
        new_sid = uuid.uuid4().hex
        session["game_id"] = new_sid
        game = InteractiveGame(level=2)
        _load_saved_config(game)
        _games[new_sid] = (game, time.time())
        return game


def _normalize_config(config: dict):
    """Convert string player-keys back to int (JSON only supports string keys)."""
    players = config.get("players")
    if isinstance(players, dict):
        config["players"] = {int(k): v for k, v in players.items()}


def _load_saved_config(game) -> None:
    """Apply the on-disk saved config to a freshly created game."""
    try:
        if _CONFIG_PATH.exists():
            saved = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            _normalize_config(saved)
            game.config.update(saved)
    except (OSError, json.JSONDecodeError, ValueError):
        pass


# ==================================================================
# Pages
# ==================================================================

@app.route("/")
def menu():
    return render_template("menu.html")


@app.route("/game")
def game_page():
    game = _get_game()
    if game.state is None:
        game.start_new_round()
    initial_state = json.dumps(game.get_state())
    return render_template("game.html", initial_state=initial_state)


@app.route("/arena")
def arena_page():
    return render_template("arena.html")


@app.route("/eval")
def eval_page():
    return render_template("eval.html")


@app.route("/favicon.ico")
def favicon():
    """Silence the browser's automatic /favicon.ico 404."""
    return ("", 204)


# ==================================================================
# Game API (existing)
# ==================================================================

@app.route("/api/state")
def api_state():
    game = _get_game()
    if game.state is None:
        game.start_new_round()
    return jsonify(game.get_state())


@app.route("/api/play", methods=["POST"])
def api_play():
    game = _get_game()
    data = request.get_json(silent=True) or {}
    card_ids = data.get("card_ids", [])
    if not isinstance(card_ids, list):
        card_ids = []
    if not card_ids:
        return jsonify(game.get_state())
    result = game.play_cards(card_ids)
    return jsonify(result)


@app.route("/api/pass", methods=["POST"])
def api_pass():
    game = _get_game()
    result = game.pass_turn()
    return jsonify(result)


@app.route("/api/start_game", methods=["POST"])
def api_start_game():
    game = _get_game()
    result = game.start_game()
    return jsonify(result)


@app.route("/api/tribute_return", methods=["POST"])
def api_tribute_return():
    """Human (as tribute receiver) submits the card to return (还贡)."""
    game = _get_game()
    data = request.get_json(silent=True) or {}
    card_ids = data.get("card_ids", [])
    if not isinstance(card_ids, list):
        card_ids = []
    result = game.submit_tribute_return(card_ids)
    return jsonify(result)


@app.route("/api/new_game", methods=["POST"])
def api_new_game():
    sid = uuid.uuid4().hex
    session["game_id"] = sid
    game = InteractiveGame(level=2)
    _load_saved_config(game)
    with _GAMES_LOCK:
        _games[sid] = (game, time.time())
    game.start_new_round()
    return jsonify(game.get_state())


@app.route("/api/hint")
def api_hint():
    game = _get_game()
    if game.state is None:
        return jsonify({"card_ids": []})
    if not game._is_human_turn():
        return jsonify({"card_ids": []})

    from ...combo_finder import ComboFinder
    from ...combo_compare import can_beat
    hand = game.state.hands[game.HUMAN_ID]
    finder = ComboFinder(hand, game.state.level)

    if game.state.table.is_empty or game.state.table.last_played_player == game.HUMAN_ID:
        combo = finder.pick_lead()
        if combo:
            return jsonify({"card_ids": [c.id for c in combo.cards]})
    else:
        combo = finder.pick_response(game.state.table.current_combo)
        if combo and can_beat(combo, game.state.table.current_combo):
            return jsonify({"card_ids": [c.id for c in combo.cards]})

    # Fallback: try any bomb
    bomb = finder._find_any_bomb()
    if bomb:
        tc = game.state.table.current_combo
        if tc is None or can_beat(bomb, tc):
            return jsonify({"card_ids": [c.id for c in bomb.cards]})
    return jsonify({"card_ids": []})


@app.route("/api/debug")
def api_debug():
    game = _get_game()
    if game.state is None:
        return jsonify({"hands": []})
    hands = []
    for p in range(4):
        cards = game.state.hands[p]
        sorted_cards = sorted(cards, key=lambda c: (c.rank.value, c.suit.value))
        hands.append({
            "player": p,
            "cards": [game._card_json(c) for c in sorted_cards],
        })
    return jsonify({"hands": hands})


@app.route("/api/log")
def api_log():
    """Query log entries with optional filters (garbage-tolerant)."""
    category = request.args.get("category")
    level = request.args.get("level")
    try:
        count = int(request.args.get("count", 200))
    except (TypeError, ValueError):
        count = 200
    count = max(1, min(count, 2000))
    from ...logging import game_logger
    entries = game_logger.query(category=category, level=level, count=count)
    return jsonify({"entries": entries, "count": len(entries)})


@app.route("/api/log/categories")
def api_log_categories():
    return jsonify({"categories": ["game", "ai", "web", "system"]})


@app.route("/api/log/clear", methods=["POST"])
def api_log_clear():
    from ...logging import game_logger
    game_logger.clear()
    return jsonify({"ok": True})


@app.route("/api/log/level", methods=["GET", "POST"])
def api_log_level():
    from ...logging import game_logger
    if request.method == "POST":
        level = request.get_json().get("level", "INFO")
        game_logger.set_level(level)
        return jsonify({"level": game_logger.get_level()})
    return jsonify({"level": game_logger.get_level()})


@app.route("/api/ai_log")
def api_ai_log():
    """Backward-compat: delegates to /api/log?category=ai."""
    from ...logging import game_logger
    entries = game_logger.query(category="ai", count=500)
    return jsonify({"entries": entries, "last_decision": []})


@app.route("/api/ai_log/clear", methods=["POST"])
def api_ai_log_clear():
    from ...logging import game_logger
    game_logger.clear()
    return jsonify({"ok": True})


@app.route("/api/llm_reasoning")
def api_llm_reasoning():
    """Return LLM reasoning + decision + fallback events per player."""
    from ...logging import game_logger
    entries = game_logger.query(category="ai", count=500)
    reasoning = {}   # pid -> {reasoning, ...}
    decisions = {}   # pid -> [{choice, cards, ...}]
    fallbacks = []   # [{player, error, elapsed_ms}]

    for e in entries:
        etype = e.get("type", "")
        pid = e.get("player", -1)
        data = e.get("data", {})

        if etype == "llm_reasoning":
            reasoning[pid] = {
                "player": pid,
                "reasoning": data.get("reasoning", ""),
            }
        elif etype == "llm_decision_end":
            decisions.setdefault(pid, []).append({
                "player": pid,
                "choice": data.get("choice", "?"),
                "cards": data.get("cards", []),
                "elapsed_ms": data.get("elapsed_ms", 0),
                "tokens": data.get("tokens", 0),
                "mode": data.get("mode", "?"),
                "model": data.get("model", "?"),
                "success_count": data.get("success_count", 0),
                "fallback_count": data.get("fallback_count", 0),
            })
        elif etype == "llm_fallback":
            fallbacks.append({
                "player": pid,
                "error": data.get("error_msg", "")[:200],
                "elapsed_ms": data.get("elapsed_ms", 0),
                "fallback_count": data.get("fallback_count", 0),
            })

    # Merge reasoning with their matching decisions
    entries_out = []
    for pid, r in reasoning.items():
        entry = dict(r)
        decs = decisions.get(pid, [])
        if decs:
            last = decs[-1]
            entry["choice"] = last["choice"]
            entry["cards"] = last["cards"]
            entry["elapsed_ms"] = last["elapsed_ms"]
            entry["tokens"] = last["tokens"]
            entry["mode"] = last["mode"]
            entry["model"] = last["model"]
            entry["success_count"] = last["success_count"]
            entry["fallback_count"] = last["fallback_count"]
        entries_out.append(entry)
    entries_out.sort(key=lambda x: x["player"])

    # Include decisions with no reasoning (e.g. fast V3 responses)
    for pid, decs in decisions.items():
        if pid not in reasoning:
            entry = decs[-1].copy()
            entry["reasoning"] = ""
            entries_out.append(entry)

    # Filter: if llm_fallback exists but no success, show that
    for fb in fallbacks:
        pid = fb["player"]
        if pid not in [e["player"] for e in entries_out]:
            entries_out.append({
                "player": pid,
                "reasoning": "",
                "choice": f'FALLBACK: {fb["error"]}',
                "cards": [],
                "elapsed_ms": fb["elapsed_ms"],
                "fallback_count": fb["fallback_count"],
            })

    entries_out.sort(key=lambda x: x["player"])
    return jsonify({
        "entries": entries_out,
        "has_llm_activity": len(entries_out) > 0 or len(fallbacks) > 0,
    })


def _strip_nones(obj):
    """Drop null values anywhere in a config payload.

    The frontend number inputs yield NaN for empty fields, which JSON
    serializes to null; storing those into config.json used to brick
    agent creation (int(None) TypeError) for every subsequent game.
    """
    if isinstance(obj, dict):
        return {k: _strip_nones(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_nones(v) for v in obj]
    if isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
        return None
    return obj


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    """Get or update AI configuration."""
    game = _get_game()
    if request.method == "POST":
        data = request.get_json(silent=True)
        if data:
            if "decider" in data:
                game.config["decider"] = _strip_nones(data["decider"])
            if "inner_model" in data:
                game.config["inner_model"] = _strip_nones(data["inner_model"])
            if "enumerator" in data:
                game.config["enumerator"] = _strip_nones(data["enumerator"])
            if "players" in data:
                players = data.get("players")
                if not isinstance(players, dict):
                    return jsonify({"error": "players must be an object"}), 400
                for pid, cfg in players.items():
                    try:
                        p = int(pid)
                    except (TypeError, ValueError):
                        return jsonify({"error": f"invalid player id: {pid}"}), 400
                    if not (0 <= p <= 3):
                        return jsonify({"error": f"player id out of range: {pid}"}), 400
                    if cfg is not None:
                        game.config["players"][p] = _strip_nones(cfg)
            if "auto_play" in data:
                game._auto_play = bool(data["auto_play"])
            # Same lock + atomic write as the tuner apply endpoint — two
            # unsynchronized writers used to clobber each other's changes.
            from ...eval.eval_api import _CONFIG_WRITE_LOCK
            import tempfile as _tempfile
            with _CONFIG_WRITE_LOCK:
                try:
                    fd, tmp = _tempfile.mkstemp(
                        dir=str(_CONFIG_PATH.parent), suffix=".json.tmp")
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(json.dumps(game.config, indent=2))
                    os.replace(tmp, _CONFIG_PATH)
                except OSError:
                    pass
    from ...ai.registry import get_schema
    schema = get_schema()
    return jsonify({"config": game.config, "schema": schema})


@app.route("/api/suggest")
def api_suggest():
    """AI suggestion: use configured MC decider to enumerate+score candidates."""
    from ...ai.player_view import PlayerView
    from ...ai.registry import create_agent_for_player
    from ...combo_compare import can_beat

    game = _get_game()
    if game.state is None or not game._is_human_turn():
        return jsonify({"candidates": [], "message": "不是你的回合"})

    pid = game.HUMAN_ID
    hand = game.state.hands[pid]
    table = game.state.table
    is_lead = table.is_empty or table.last_played_player == pid
    table_combo = table.current_combo

    # Use configured agent (default: MCDecider with FullEnumerator)
    view = PlayerView(game.state, pid, game.action_log)
    agent = create_agent_for_player(game.config, pid)

    # Try analyze() first (MCDecider/ISMCTSDecider), fallback to choose_play()
    if hasattr(agent, 'analyze'):
        result = agent.analyze(view)
        results = []
        for c in result.candidates:
            entry = {
                "type": c.combo_type,
                "type_cn": "过牌" if c.combo_type == "PASS" else _COMBO_TYPE_CN.get(
                    _combo_type_value(c.combo_type), c.combo_type),
                "cards": c.cards,
                "length": len(c.card_ids),
                "is_bomb": c.combo_type in ("NORMAL_BOMB", "ROCKET"),
                "win_rate": c.win_rate or 0,
                "score": c.score,
            }
            results.append(entry)
    else:
        # Non-MC agents — just list candidates without scoring
        from ...combo_finder import ComboFinder
        from ...ai.agent import _generate_lead_candidates, _enumerate_responses
        level = game.state.level
        finder = ComboFinder(hand, level)
        if is_lead:
            candidates = _generate_lead_candidates(finder, hand)
        else:
            candidates = _enumerate_responses(hand, table_combo, finder, level)
        results = []
        for c in candidates:
            if not is_lead and not can_beat(c, table_combo):
                continue
            results.append({
                "type": c.combo_type.name,
                "type_cn": _COMBO_TYPE_CN.get(c.combo_type.value, c.combo_type.name),
                "cards": [x.display for x in c.cards],
                "length": c.length,
                "is_bomb": c.is_bomb,
                "win_rate": 0,
                "score": None,
            })

    results.sort(key=lambda c: c.get("win_rate", 0), reverse=True)

    return jsonify({
        "candidates": results,
        "hand_size": len(hand),
        "message": f"分析了 {len(results)} 个候选",
    })


def _combo_type_value(name: str) -> int:
    """Map combo type name string back to int for _COMBO_TYPE_CN lookup."""
    from ...combo import ComboType
    try:
        return ComboType[name].value
    except KeyError:
        return 0


@app.route("/api/evaluate", methods=["POST"])
def api_evaluate():
    """Evaluate selected cards using Monte Carlo simulation."""
    from ...combo_parser import ComboParser
    from ...combo_compare import can_beat

    game = _get_game()
    if game.state is None:
        return jsonify({"error": "游戏未开始"})

    data = request.get_json() or {}
    card_ids = data.get("card_ids", [])
    if not card_ids:
        # No cards selected — check if human has any valid play
        hand = game.state.hands[game.HUMAN_ID]
        table_combo = game.state.table.current_combo
        is_lead = (table_combo is None or game.state.table.last_played_player == game.HUMAN_ID)
        if is_lead:
            return jsonify({"info": "你是首家，可任意出牌"})

        # Check if any card in hand can beat the table
        from ...combo_finder import ComboFinder
        finder = ComboFinder(hand, game.state.level)
        response = finder.pick_response(table_combo)
        bomb = finder._find_any_bomb()
        if not response and not bomb:
            return jsonify({"info": "手牌中没有任何能压过牌桌的牌型，只能过牌"})
        return jsonify({"info": "请选中要评估的牌"})

    hand = game.state.hands[game.HUMAN_ID]
    cards = [c for c in hand if c.id in card_ids]
    if len(cards) != len(card_ids):
        return jsonify({"error": "选中的牌不在手牌中"})

    parser = ComboParser(game.state.level)
    combo = parser.parse(cards)
    if combo is None:
        return jsonify({"error": "选中的牌不构成合法牌型"})

    table_combo = game.state.table.current_combo
    is_lead = (table_combo is None or game.state.table.last_played_player == game.HUMAN_ID)
    if not is_lead and not can_beat(combo, table_combo):
        return jsonify({"error": "打不过牌桌上的牌型"})

    # Run MC to score this specific play
    from ...ai.player_view import PlayerView
    from ...ai.agent import MonteCarloAgent
    view = PlayerView(game.state, game.HUMAN_ID, game.action_log)
    mc_agent = MonteCarloAgent(num_samples=60, time_limit_ms=6000)
    scored = mc_agent.score_candidates(view, [combo], can_pass=False)

    win_rate = scored[0][1] if scored else None

    from ...ai.hand_eval import estimate_rounds
    used_ids = {c.id for c in cards}
    hand_after = tuple(c for c in hand if c.id not in used_ids)

    return jsonify({
        "valid": True,
        "combo_type": combo.combo_type.name,
        "cards": [c.display for c in cards],
        "win_rate": win_rate,
        "rounds_before": estimate_rounds(hand, game.state.level),
        "rounds_after": estimate_rounds(hand_after, game.state.level),
        "is_bomb": combo.is_bomb,
    })


@app.route("/api/auto_play", methods=["POST"])
def api_auto_play():
    """Toggle full auto-play mode (guarded: refuses at round-over / pending
    tribute / before start — arming the AI loop in those states used to
    re-settle the round or bypass the tribute exchange)."""
    game = _get_game()
    data = request.get_json(silent=True) or {}
    return jsonify(game.set_auto_play(bool(data.get("enabled", False))))


@app.route("/api/new_round", methods=["POST"])
def api_new_round():
    game = _get_game()
    result = game.start_new_round()
    return jsonify(result)


# ==================================================================
# (Legacy prototype arena endpoints removed — the live arena API lives in
#  arena_api.py under /api/arena/*; these two had no frontend callers.)
# ==================================================================

# Canonical map lives in constants.COMBO_TYPE_CN.
from ...constants import COMBO_TYPE_CN as _COMBO_TYPE_CN


# ==================================================================
# Main
# ==================================================================

if __name__ == "__main__":
    import os
    import socket
    import threading
    import webbrowser

    debug = os.environ.get("FLASK_DEBUG", "0") == "1"

    # Pick the first free port from GUANDAN_PORT (default 8765) upward —
    # other services may have taken our usual port.
    port = int(os.environ.get("GUANDAN_PORT", "8765"))

    def _port_in_use(p: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            return s.connect_ex(("127.0.0.1", p)) == 0

    for _ in range(20):
        if not _port_in_use(port):
            break
        print(f"  端口 {port} 被占用，尝试 {port + 1} ...")
        port += 1

    url = f"http://localhost:{port}/"
    print("=" * 50)
    print("  掼蛋 Guandan — Web UI")
    print(f"  Main menu: {url}")
    print(f"  Game:      http://localhost:{port}/game")
    print(f"  Arena:     http://localhost:{port}/arena")
    print(f"  Eval Lab:  http://localhost:{port}/eval")
    print("=" * 50)

    # Open the browser automatically once the server is accepting connections
    # (unless GUANDAN_NO_BROWSER=1, e.g. when launched by scripts).
    if os.environ.get("GUANDAN_NO_BROWSER", "") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    app.run(debug=debug, host="0.0.0.0", port=port)
