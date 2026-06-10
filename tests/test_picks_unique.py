"""Unique team ownership."""

from __future__ import annotations

import pytest

from wcsweep.services import picks


def test_one_team_one_player(session, make_player, make_team):
    a = make_player("Alice")
    b = make_player("Bob")
    brazil = make_team("Brazil")

    picks.assign(session, a.id, brazil.id, round_no=1)
    with pytest.raises(picks.PickError):
        picks.assign(session, b.id, brazil.id, round_no=1)


def test_available_excludes_taken(session, make_player, make_team):
    a = make_player("Alice")
    brazil = make_team("Brazil")
    france = make_team("France")
    picks.assign(session, a.id, brazil.id, round_no=1)

    available = {t.id for t in picks.available_teams(session)}
    assert france.id in available
    assert brazil.id not in available


def test_player_cap_enforced(session, make_player, make_team, monkeypatch):
    from wcsweep.config import get_settings

    get_settings.cache_clear()
    a = make_player("Alice")
    t1, t2, t3 = make_team("A"), make_team("B"), make_team("C")
    picks.assign(session, a.id, t1.id, round_no=1)
    picks.assign(session, a.id, t2.id, round_no=2)
    with pytest.raises(picks.PickError):
        picks.assign(session, a.id, t3.id, round_no=2)


def test_release_returns_teams(session, make_player, make_team):
    a = make_player("Alice")
    t1 = make_team("Brazil")
    picks.assign(session, a.id, t1.id, round_no=1)
    assert picks.release_player_picks(session, a.id) == 1
    assert t1.id in {t.id for t in picks.available_teams(session)}
