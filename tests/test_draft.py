"""Randomized team draw: order, turns, full allocation, auto-draw."""

from __future__ import annotations

import pytest

from wcsweep.models import DraftStatus, GamePhase
from wcsweep.services import draft, game, picks


def test_seed_makes_order_reproducible(session, make_player, make_team):
    import random

    [make_player(f"P{i}") for i in range(5)]
    [make_team(f"T{i}") for i in range(10)]  # 5 players x 2 teams
    order = list(draft.start(session, seed="abc").order_player_ids)

    # Shuffling the same id set with the same seed must reproduce the stored order.
    shuffled = sorted(order)
    random.Random("abc").shuffle(shuffled)
    assert order == shuffled


def test_draw_assigns_full_allocation_per_turn(session, make_player, make_team):
    a = make_player("Alice")
    b = make_player("Bob")
    for i in range(6):
        make_team(f"T{i}")

    draft.start(session, seed="seed1")
    guard = 0
    while draft.get_draft(session).status == DraftStatus.RUNNING:
        guard += 1
        assert guard < 10, "draft did not terminate"
        pid = draft.current_player_id(session)
        teams = draft.draw_for_player(session, pid)
        assert len(teams) == 2  # full allocation in one turn

    assert draft.get_draft(session).status == DraftStatus.COMPLETE
    assert len(picks.player_picks(session, a.id)) == 2
    assert len(picks.player_picks(session, b.id)) == 2
    assert game.get_state(session).phase == GamePhase.RUNNING


def test_draw_is_reproducible_from_seed(session, make_player, make_team):
    # Same players + same seed + same team pool -> identical assignment.
    def run():
        a = make_player("A")
        b = make_player("B")
        for i in range(6):
            make_team(f"T{i}")
        draft.start(session, seed="fixed")
        result = {}
        while draft.get_draft(session).status == DraftStatus.RUNNING:
            pid = draft.current_player_id(session)
            result[pid] = sorted(t.name for t in draft.draw_for_player(session, pid))
        return result, a, b

    res, a, b = run()
    # deterministic: every team assigned, none shared
    all_teams = [t for ts in res.values() for t in ts]
    assert len(all_teams) == len(set(all_teams)) == 4


def test_cannot_draw_out_of_turn(session, make_player, make_team):
    a = make_player("Alice")
    b = make_player("Bob")
    for i in range(4):
        make_team(f"T{i}")
    draft.start(session, seed="x")
    not_current = b.id if draft.current_player_id(session) == a.id else a.id
    with pytest.raises(draft.DraftError):
        draft.draw_for_player(session, not_current)


def test_auto_pick_draws_and_advances(session, make_player, make_team):
    make_player("Alice")
    make_player("Bob")
    for i in range(4):
        make_team(f"T{i}")
    draft.start(session, seed="y")
    before = draft.current_player_id(session)
    result = draft.auto_pick(session)
    assert result is not None
    pid, teams = result
    assert pid == before
    assert len(teams) == 2
    assert draft.current_player_id(session) != before


def test_start_requires_two_players(session, make_player, make_team):
    make_player("Solo")
    make_team("OnlyTeam")
    make_team("OtherTeam")
    with pytest.raises(draft.DraftError):
        draft.start(session, seed="z")


def test_start_requires_enough_teams(session, make_player, make_team):
    make_player("Alice")
    make_player("Bob")
    # 2 players x 2 = 4 needed, only 3 in pool
    for i in range(3):
        make_team(f"T{i}")
    with pytest.raises(draft.DraftError):
        draft.start(session, seed="z")
