"""Scoring rules and ledger rebuild."""

from __future__ import annotations

from wcsweep.models import Match, MatchStatus, Outcome, Stage
from wcsweep.services import scoring


def _match(home, away, hs, as_, *, pens=None, stage=Stage.GROUP):
    return Match(
        id=None,
        stage=stage,
        home_team_id=home,
        away_team_id=away,
        home_score=hs,
        away_score=as_,
        decided_by_pens=pens is not None,
        pen_winner_team_id=pens,
        status=MatchStatus.FINISHED,
    )


def test_win_draw_loss_points():
    m = _match(1, 2, 2, 0)
    assert scoring.compute_outcome(m, 1, ko_penalty_as_draw=False).points == 3
    assert scoring.compute_outcome(m, 2, ko_penalty_as_draw=False).points == 0

    draw = _match(1, 2, 1, 1)
    assert scoring.compute_outcome(draw, 1, ko_penalty_as_draw=False).outcome == Outcome.DRAW
    assert scoring.compute_outcome(draw, 1, ko_penalty_as_draw=False).points == 1


def test_penalty_winner_gets_win_by_default():
    # Level after ET (1-1), team 2 wins on penalties.
    m = _match(1, 2, 1, 1, pens=2, stage=Stage.R16)
    assert scoring.compute_outcome(m, 2, ko_penalty_as_draw=False).outcome == Outcome.WIN
    assert scoring.compute_outcome(m, 2, ko_penalty_as_draw=False).points == 3
    assert scoring.compute_outcome(m, 1, ko_penalty_as_draw=False).outcome == Outcome.LOSS


def test_penalty_as_draw_flag():
    m = _match(1, 2, 1, 1, pens=2, stage=Stage.R16)
    assert scoring.compute_outcome(m, 2, ko_penalty_as_draw=True).outcome == Outcome.DRAW
    assert scoring.compute_outcome(m, 1, ko_penalty_as_draw=True).points == 1


def test_goals_for_against():
    m = _match(1, 2, 3, 1)
    r = scoring.compute_outcome(m, 1, ko_penalty_as_draw=False)
    assert (r.goals_for, r.goals_against) == (3, 1)
    r2 = scoring.compute_outcome(m, 2, ko_penalty_as_draw=False)
    assert (r2.goals_for, r2.goals_against) == (1, 3)


def test_rebuild_ledger_is_idempotent(session, make_player, make_team):
    from wcsweep.models import Pick
    from wcsweep.services import picks

    p = make_player("Alice")
    t1 = make_team("Brazil")
    t2 = make_team("France")
    picks.assign(session, p.id, t1.id, round_no=1)
    picks.assign(session, p.id, t2.id, round_no=2)

    m = Match(
        stage=Stage.GROUP,
        home_team_id=t1.id,
        away_team_id=t2.id,
        home_score=2,
        away_score=1,
        status=MatchStatus.FINISHED,
    )
    session.add(m)
    session.flush()

    n1 = scoring.rebuild_ledger(session)
    n2 = scoring.rebuild_ledger(session)
    assert n1 == n2 == 2  # both owned teams played the same match

    board = scoring.leaderboard(session)
    assert len(board) == 1
    # Brazil won (3) + France lost (0) = 3, both owned by Alice
    assert board[0].points == 3
    assert board[0].wins == 1
    _ = Pick  # imported for clarity
