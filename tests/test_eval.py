"""Tests for the Eval Lab framework (elo, metrics, scenario bank, self-play)."""

import pytest

from guandan.eval.elo import expected_score, update as elo_update
from guandan.eval.metrics import (
    wilson_interval, is_correct, agreement_rate, entropy, regret,
)
from guandan.eval.scenario_bank import (
    load_curated_scenarios, grade_difficulty, ScenarioBank,
    generate_synthetic_scenarios,
)
from guandan.eval.selfplay import build_player_agent, run_match
from guandan.arena.scenarios import Scenario


# ==================================================================
# Elo
# ==================================================================

class TestElo:
    def test_expected_equal(self):
        assert expected_score(1500, 1500) == pytest.approx(0.5)

    def test_expected_favorite(self):
        assert expected_score(1600, 1400) > 0.5
        assert expected_score(1400, 1600) < 0.5

    def test_update_win_lose_symmetric(self):
        ra = elo_update(1500, 1500, 1.0)
        rb = elo_update(1500, 1500, 0.0)
        assert ra > 1500 > rb
        assert ra - 1500 == pytest.approx(1500 - rb)


# ==================================================================
# Metrics
# ==================================================================

class TestMetrics:
    def test_wilson_bounds(self):
        low, high = wilson_interval(5, 10)
        assert 0 < low < 0.5 < high < 1

    def test_wilson_zero_n(self):
        assert wilson_interval(0, 0) == (0.0, 0.0)

    def test_is_correct_rank_based(self):
        # Pair of 8s (ids 6,19) matches expected pair of 8s (ids 32,45) by rank
        assert is_correct([6, 19], [32, 45], expected_pass=False, pass_chosen=False)
        assert not is_correct([6, 14], [32, 45], expected_pass=False, pass_chosen=False)

    def test_is_correct_pass(self):
        assert is_correct([], None, expected_pass=True, pass_chosen=True)
        assert not is_correct([], None, expected_pass=True, pass_chosen=False)

    def test_agreement_and_entropy(self):
        assert agreement_rate(['a', 'a', 'b']) == pytest.approx(2 / 3)
        assert entropy(['a', 'a', 'a']) == pytest.approx(0.0)
        assert entropy([]) == 0.0

    def test_regret(self):
        assert regret(0.3, 0.9) == pytest.approx(0.6)
        assert regret(0.9, 0.3) == 0.0


# ==================================================================
# Scenario bank
# ==================================================================

class TestScenarioBank:
    def test_curated_loaded(self):
        scenarios = load_curated_scenarios()
        assert len(scenarios) >= 12
        assert all(s.source == "curated" for s in scenarios)

    def test_grade_difficulty(self):
        graded = grade_difficulty({"a": 0.9, "b": 0.5, "c": 0.1})
        assert graded == {"a": "easy", "b": "medium", "c": "hard"}

    def test_bank_add_and_filter(self):
        bank = ScenarioBank()
        s = Scenario(id="x", name="X", category="endgame", description="",
                     hand=[1], source="synthetic", ground_truth="exact")
        bank.add([s])
        assert bank.get("x") is s
        assert len(bank.filter(source="synthetic")) == 1
        assert len(bank.filter(source="curated")) == 0

    def test_synthetic_generation(self):
        scenarios = generate_synthetic_scenarios(
            n=2, max_cards_per_player=4, seed=0, time_limit_ms=3000)
        assert len(scenarios) >= 1
        for s in scenarios:
            assert s.source == "synthetic"
            assert s.ground_truth == "exact"
            assert s.expected_play is not None
            # expected_play must reference cards in the hand
            assert set(s.expected_play).issubset(set(s.hand))


# ==================================================================
# Self-play
# ==================================================================

class TestSelfPlay:
    def test_build_player_agent_blind(self):
        from guandan.ai.agent import BlindAgent
        agent = build_player_agent({"id": "blind"})
        assert isinstance(agent, BlindAgent)
        assert hasattr(agent, "choose_play")

    def test_build_player_agent_mc(self):
        from guandan.ai.mc_decider import MCDecider
        agent = build_player_agent({"id": "mc", "num_samples": 10, "time_limit_ms": 1000})
        assert isinstance(agent, MCDecider)

    def test_run_match_records_config_and_seed(self):
        m = run_match({"id": "blind"}, {"id": "blind"}, n_games=1, base_seed=0)
        assert m.n_games == 1
        assert m.a_wins + m.b_wins == 1
        assert len(m.games) == 1
        assert m.games[0].seed == 0
        d = m.to_dict()
        # honest parameter recording: configs embedded in the result
        assert d["a"] == {"id": "blind"}
        assert d["b"] == {"id": "blind"}
        assert d["n_games"] == 1


# ==================================================================
# Progress callbacks
# ==================================================================

class TestProgressCallbacks:
    def test_run_match_progress(self):
        from guandan.eval.selfplay import run_match
        calls = []

        def cb(done, total, msg):
            calls.append((done, total))

        run_match({"id": "blind"}, {"id": "blind"}, n_games=2, base_seed=0, progress_cb=cb)
        # Move-based progress: many callbacks, monotonic 'done', final one complete
        assert len(calls) >= 2
        dones = [d for d, _ in calls]
        assert dones == sorted(dones)
        assert all(total == dones[-1] for _, total in calls)
        assert calls[-1][0] == calls[-1][1]  # final callback reports completion

    def test_run_match_live_cb(self):
        from guandan.eval.selfplay import run_match
        details = []

        def live(detail):
            details.append(detail)

        run_match({"id": "blind"}, {"id": "blind"}, n_games=1, base_seed=0, live_cb=live)
        assert len(details) >= 10  # a full game is many moves
        d = details[-1]
        # Dashboard keys present
        for key in ("game", "n_games", "move", "trick", "hand_sizes",
                    "last_player", "last_action", "last_combo_type",
                    "last_move_ms", "avg_move_ms", "moves_per_sec",
                    "think_ms", "move_ms_hist", "scores"):
            assert key in d, f"missing key: {key}"
        assert len(d["hand_sizes"]) == 4
        assert d["last_action"] in ("play", "pass")
        assert d["think_ms"][0] >= 0 and d["think_ms"][1] >= 0
        assert len(d["move_ms_hist"]) <= 60  # sparkline window is bounded
        # During the last live callback the game hasn't been patched yet → the
        # in-progress placeholder (-2); completed games carry 0/1/-1.
        assert all(s in (-2, -1, 0, 1) for s in d["scores"])
        # hand_sizes sum decreases over the game (cards leave hands)
        first = details[0]
        assert sum(first["hand_sizes"]) >= sum(d["hand_sizes"])

    def test_round_robin_progress_passthrough(self):
        from guandan.eval.selfplay import run_round_robin
        calls = []
        run_round_robin([{"id": "blind"}, {"id": "blind"}], n_games=1,
                        base_seed=0, progress_cb=lambda d, t, m: calls.append(d))
        # Sub-match move progress folds into pair slices → more than 1 call per pair
        assert len(calls) > 1
        assert calls[-1] == 1  # final: pair 1 of 1 complete (done == n_pairs)

    def test_game_on_move_signature(self):
        """Game.on_move receives (game, player:int, combo|None)."""
        from guandan.game import Game
        from guandan.eval.selfplay import build_player_agent
        seen = []

        def cb(game, player, combo):
            seen.append((player, combo))
            if len(seen) >= 3:
                raise KeyboardInterrupt  # stop the game early

        agents = [build_player_agent({"id": "blind"}) for _ in range(4)]
        game = Game(agents, level=2, on_move=cb)
        try:
            game.play_game()
        except KeyboardInterrupt:
            pass
        assert len(seen) >= 3
        for player, combo in seen:
            assert isinstance(player, int) and 0 <= player <= 3
            assert combo is None or hasattr(combo, "combo_type")

    def test_benchmark_progress(self):
        from guandan.eval.eval_api import run_scenario_benchmark
        from guandan.eval.scenario_bank import load_curated_scenarios
        scenarios = load_curated_scenarios()[:3]
        calls = []

        def cb(done, total, msg):
            calls.append((done, total))

        run_scenario_benchmark(scenarios, [{"id": "blind"}], progress_cb=cb)
        assert calls == [(1, 3), (2, 3), (3, 3)]

    def test_generate_progress(self):
        from guandan.eval.scenario_bank import generate_synthetic_scenarios
        called = []

        def cb(done, total, msg):
            called.append((done, total))

        generate_synthetic_scenarios(n=2, max_cards_per_player=4, seed=0,
                                     time_limit_ms=2000, progress_cb=cb)
        assert len(called) >= 1
        assert all(total == 2 for _, total in called)


# ==================================================================
# ISMCTS regression — backpropagation perspective fix
# ==================================================================

class TestISMCTSBackprop:
    def test_single_reward_backprop(self):
        """The previous bug credited a child from the NEXT player's perspective,
        inverting win rates at odd depths. The fix propagates one reward
        (root player won?) to every node on the path."""
        from guandan.ai.ismcts import ISMCTSDecider, _Node

        decider = ISMCTSDecider(max_iterations=1, time_limit_ms=1)
        root = _Node(None, 0, None, 1.4)
        child = _Node(None, 0, None, 1.4)
        child.parent = root

        decider._backpropagate(child, True, 0)

        assert child.visits == 1 and child.wins == 1
        assert root.visits == 1 and root.wins == 1
