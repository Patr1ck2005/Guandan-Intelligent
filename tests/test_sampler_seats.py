"""Sampler seat-matrix tests (audit M1: H1/H2 seat hardcoding).

ConstrainedSampler and CardTracker.sample_opponent_hands used to hardcode
seats [1, 2, 3] as "the opponents": from any seat other than 0 the decider's
OWN seat got sampled, seat 0's hand was silently blanked, and every MC
simulation downstream ran against noise. These tests pin the fix: for every
seat the sampled key set must be exactly the other three seats with exact
hand sizes.
"""

import random

import pytest

from guandan.card import Card
from guandan.combo_parser import ComboParser
from guandan.deck import Deck
from guandan.game_state import GameState
from guandan.ai.action_log import ActionLog
from guandan.ai.constrained_sampler import ConstrainedSampler
from guandan.ai.player_view import PlayerView


@pytest.fixture(scope="module")
def hands():
    rng = random.Random(7)
    return Deck.deal(Deck.shuffle(Deck.create(), rng=rng))


def _view(hands, seat: int) -> PlayerView:
    state = GameState(level=2, round_number=1, hands=hands,
                      current_player=seat, trick_number=1)
    log = ActionLog()
    # A pass from the next seat so the constrained path activates.
    combo = ComboParser(2).parse([Card.from_id(20)])
    log.record_pass((seat + 1) % 4, None, combo)
    # Also a pass from the viewer itself — must NOT constrain anyone.
    log.record_pass(seat, None, combo)
    return PlayerView(state, seat, log)


@pytest.mark.parametrize("seat", range(4))
def test_constrained_sampler_opponent_set(hands, seat):
    view = _view(hands, seat)
    sampled = ConstrainedSampler().sample(view)
    expected = {p for p in range(4) if p != seat}
    assert set(sampled.keys()) == expected
    for p in expected:
        assert len(sampled[p]) == len(hands[p])


@pytest.mark.parametrize("seat", range(4))
def test_tracker_sample_opponent_hands_seats(hands, seat):
    view = _view(hands, seat)
    sampled = view.tracker.sample_opponent_hands()
    expected = {p for p in range(4) if p != seat}
    assert set(sampled.keys()) == expected
    for p in expected:
        assert len(sampled[p]) == len(hands[p])


@pytest.mark.parametrize("seat", range(4))
def test_mc_decider_seat2_simulation_not_blank(hands, seat):
    """End-to-end guard: MCDecider with the constrained sampler must never
    hand the simulator an empty opponent hand on any seat."""
    from guandan.ai.candidate_enum import FullEnumerator
    from guandan.ai.mc_decider import MCDecider
    from guandan.ai.sampler import create_sampler

    view = _view(hands, seat)
    decider = MCDecider(sampler=create_sampler("constrained"),
                        inner=None, enumerator=FullEnumerator(),
                        num_samples=2, time_limit_ms=2000)
    result = decider.analyze(view)
    assert result is not None
    # Every simulated candidate must have produced actual samples (not the
    # blanked-hand degenerate case where sims trivially end).
    for cr in result.candidates:
        assert cr.detail["samples"] == 2
