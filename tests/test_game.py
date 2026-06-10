"""Game-end detection and winner resolution."""

from __future__ import annotations

from wcsweep.models import GamePhase, Match, MatchStatus, Stage
from wcsweep.services import game, picks


def _finished(session, home_id, away_id, hs, as_):
    m = Match(stage=Stage.GROUP, home_team_id=home_id, away_team_id=away_id,
              home_score=hs, away_score=as_, status=MatchStatus.FINISHED)
    session.add(m)
    session.flush()
    return m


def test_cannot_end_while_owned_team_has_a_fixture(session, make_player, make_team):
    a = make_player("Alice")
    t1, t2 = make_team("Brazil"), make_team("France")
    picks.assign(session, a.id, t1.id, round_no=1)
    # An unfinished match involving an owned team -> can't end.
    m = Match(stage=Stage.GROUP, home_team_id=t1.id, away_team_id=t2.id,
              status=MatchStatus.SCHEDULED)
    session.add(m)
    session.flush()
    assert game.can_end(session) is False


def test_can_end_when_no_relevant_fixtures_remain(session, make_player, make_team):
    a = make_player("Alice")
    t1, t2 = make_team("Brazil"), make_team("France")
    picks.assign(session, a.id, t1.id, round_no=1)
    _finished(session, t1.id, t2.id, 2, 0)
    assert game.can_end(session) is True


def test_declare_winner_picks_leader(session, make_player, make_team):
    a = make_player("Alice")
    b = make_player("Bob")
    ta, tb = make_team("Brazil"), make_team("France")
    picks.assign(session, a.id, ta.id, round_no=1)
    picks.assign(session, b.id, tb.id, round_no=1)
    _finished(session, ta.id, tb.id, 3, 0)  # Alice's team wins
    game.lock_picks(session)
    from wcsweep.services import scoring
    scoring.rebuild_ledger(session)

    winner = game.declare_winner(session, force=True)
    assert winner.id == a.id
    assert game.get_state(session).phase == GamePhase.FINISHED


def test_declare_winner_breaks_tie_deterministically(session, make_player, make_team):
    a = make_player("Alice")
    b = make_player("Bob")
    ta, tb = make_team("Brazil"), make_team("France")
    picks.assign(session, a.id, ta.id, round_no=1)
    picks.assign(session, b.id, tb.id, round_no=1)
    _finished(session, ta.id, tb.id, 1, 1)  # draw -> both 1 pt, full tie
    from wcsweep.services import scoring
    scoring.rebuild_ledger(session)

    winner = game.declare_winner(session, force=True)
    assert winner.id in (a.id, b.id)
    state = game.get_state(session)
    assert state.tiebreak_seed is not None  # recorded for auditability
