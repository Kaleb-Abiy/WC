"""Pure scoring logic + ledger (re)build.

`compute_outcome` is intentionally side-effect free so it can be unit-tested in
isolation. `rebuild_ledger` is idempotent: it wipes and re-derives all score events
from finished matches, so it can be run any time after a result changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    Match,
    MatchStatus,
    Outcome,
    Pick,
    ScoreEvent,
)

POINTS = {Outcome.WIN: 3, Outcome.DRAW: 1, Outcome.LOSS: 0}


@dataclass(frozen=True)
class TeamResult:
    outcome: Outcome
    points: int
    goals_for: int
    goals_against: int


def compute_outcome(match: Match, team_id: int, *, ko_penalty_as_draw: bool) -> TeamResult:
    """Compute one team's result in a finished match.

    Scores are taken after extra time. If the match was decided on penalties, the
    behaviour depends on `ko_penalty_as_draw`:
      - False (our default): the shootout winner gets a WIN (3), loser a LOSS (0).
      - True: both teams get a DRAW (1).
    """
    if team_id not in (match.home_team_id, match.away_team_id):
        raise ValueError("team did not play in this match")
    if match.home_score is None or match.away_score is None:
        raise ValueError("match has no score")

    is_home = team_id == match.home_team_id
    gf = match.home_score if is_home else match.away_score
    ga = match.away_score if is_home else match.home_score

    if gf > ga:
        outcome = Outcome.WIN
    elif gf < ga:
        outcome = Outcome.LOSS
    else:
        # Level after extra time.
        if match.decided_by_pens and not ko_penalty_as_draw:
            outcome = (
                Outcome.WIN if match.pen_winner_team_id == team_id else Outcome.LOSS
            )
        else:
            outcome = Outcome.DRAW

    return TeamResult(outcome=outcome, points=POINTS[outcome], goals_for=gf, goals_against=ga)


def rebuild_ledger(session: Session) -> int:
    """Wipe and rebuild score_events from all finished matches. Returns event count.

    Only matches involving a picked (owned) team produce events.
    """
    ko_as_draw = get_settings().ko_penalty_as_draw

    # team_id -> player_id for owned teams only
    owned: dict[int, int] = {
        pick.team_id: pick.player_id for pick in session.scalars(select(Pick))
    }

    session.query(ScoreEvent).delete()

    finished = session.scalars(
        select(Match).where(Match.status == MatchStatus.FINISHED)
    )
    count = 0
    for match in finished:
        if match.home_score is None or match.away_score is None:
            continue
        for team_id in (match.home_team_id, match.away_team_id):
            player_id = owned.get(team_id)
            if player_id is None:
                continue
            res = compute_outcome(match, team_id, ko_penalty_as_draw=ko_as_draw)
            session.add(
                ScoreEvent(
                    player_id=player_id,
                    team_id=team_id,
                    match_id=match.id,
                    outcome=res.outcome,
                    points=res.points,
                    goals_for=res.goals_for,
                    goals_against=res.goals_against,
                )
            )
            count += 1
    session.flush()
    return count


@dataclass(frozen=True)
class Standing:
    player_id: int
    points: int
    wins: int
    goal_diff: int
    goals_for: int


def leaderboard(session: Session) -> list[Standing]:
    """Derive standings from the ledger, sorted by the tie-break order in DESIGN §5."""
    rows = session.scalars(select(ScoreEvent))
    agg: dict[int, dict[str, int]] = {}
    for ev in rows:
        s = agg.setdefault(
            ev.player_id, {"points": 0, "wins": 0, "gf": 0, "ga": 0}
        )
        s["points"] += ev.points
        s["wins"] += 1 if ev.outcome == Outcome.WIN else 0
        s["gf"] += ev.goals_for
        s["ga"] += ev.goals_against

    standings = [
        Standing(
            player_id=pid,
            points=s["points"],
            wins=s["wins"],
            goal_diff=s["gf"] - s["ga"],
            goals_for=s["gf"],
        )
        for pid, s in agg.items()
    ]
    standings.sort(
        key=lambda s: (s.points, s.wins, s.goal_diff, s.goals_for), reverse=True
    )
    return standings
