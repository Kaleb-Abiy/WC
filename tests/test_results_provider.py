"""football-data.org match parsing (pure mapping, no network)."""

from __future__ import annotations

from wcsweep.models import MatchStatus, Stage
from wcsweep.services.results.footballdata import map_match


def _raw(**over):
    base = {
        "id": 1,
        "stage": "GROUP_STAGE",
        "status": "FINISHED",
        "utcDate": "2026-06-11T19:00:00Z",
        "homeTeam": {"id": 100, "name": "Brazil"},
        "awayTeam": {"id": 200, "name": "France"},
        "score": {"duration": "REGULAR", "winner": "HOME_TEAM",
                  "fullTime": {"home": 2, "away": 1}},
    }
    base.update(over)
    return base


def test_basic_finished_result():
    m = map_match(_raw())
    assert m.api_match_id == 1
    assert m.stage == Stage.GROUP
    assert m.status == MatchStatus.FINISHED
    assert (m.home_score, m.away_score) == (2, 1)
    assert m.home.api_id == 100 and m.away.name == "France"
    assert not m.decided_by_pens
    assert m.kickoff_utc is not None and m.kickoff_utc.tzinfo is None


def test_stage_and_status_mapping():
    assert map_match(_raw(stage="LAST_16")).stage == Stage.R16
    assert map_match(_raw(stage="LAST_32")).stage == Stage.R32
    assert map_match(_raw(stage="QUARTER_FINALS")).stage == Stage.QF
    assert map_match(_raw(stage="FINAL")).stage == Stage.FINAL
    assert map_match(_raw(status="TIMED")).status == MatchStatus.SCHEDULED
    assert map_match(_raw(status="IN_PLAY")).status == MatchStatus.LIVE


def test_penalty_shootout_uses_winner_field():
    raw = _raw(
        stage="LAST_16",
        score={
            "duration": "PENALTY_SHOOTOUT",
            "winner": "AWAY_TEAM",
            "fullTime": {"home": 1, "away": 1},
            "penalties": {"home": 3, "away": 4},
        },
    )
    m = map_match(raw)
    assert m.decided_by_pens is True
    assert m.pen_winner == "AWAY"
    # scores are the after-ET level score, not the shootout
    assert (m.home_score, m.away_score) == (1, 1)


def test_penalty_falls_back_to_penalty_count():
    raw = _raw(
        score={
            "duration": "PENALTY_SHOOTOUT",
            "winner": None,
            "fullTime": {"home": 0, "away": 0},
            "penalties": {"home": 5, "away": 3},
        },
    )
    m = map_match(raw)
    assert m.decided_by_pens and m.pen_winner == "HOME"


def test_level_in_regular_time_is_not_pens():
    m = map_match(_raw(score={"duration": "REGULAR", "winner": "DRAW",
                              "fullTime": {"home": 1, "away": 1}}))
    assert not m.decided_by_pens and m.pen_winner is None
