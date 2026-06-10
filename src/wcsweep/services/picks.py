"""Team picks. Uniqueness (one team -> one player) is enforced at the DB level
by UNIQUE(team_id); these helpers add availability queries and friendly errors."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Pick, Team


class PickError(Exception):
    pass


def taken_team_ids(session: Session) -> set[int]:
    return set(session.scalars(select(Pick.team_id)))


def available_teams(session: Session) -> list[Team]:
    taken = taken_team_ids(session)
    return [
        t
        for t in session.scalars(select(Team).order_by(Team.group_letter, Team.name))
        if t.id not in taken
    ]


def player_picks(session: Session, player_id: int) -> list[Pick]:
    return list(
        session.scalars(
            select(Pick)
            .where(Pick.player_id == player_id)
            .order_by(Pick.pick_order)
        )
    )


def assign(
    session: Session, player_id: int, team_id: int, round_no: int
) -> Pick:
    """Assign a team to a player. Raises PickError if the team is gone or the
    player is already full. Relies on UNIQUE(team_id) to win pick races."""
    existing = player_picks(session, player_id)
    cap = get_settings().teams_per_player
    if len(existing) >= cap:
        raise PickError("You already hold your maximum number of teams.")

    team = session.get(Team, team_id)
    if team is None:
        raise PickError("Unknown team.")
    if team_id in taken_team_ids(session):
        raise PickError(f"{team.name} has already been taken.")

    pick = Pick(
        player_id=player_id,
        team_id=team_id,
        pick_order=len(existing) + 1,
        round_no=round_no,
    )
    session.add(pick)
    try:
        session.flush()
    except IntegrityError as exc:  # lost the race on UNIQUE(team_id)
        session.rollback()
        raise PickError(f"{team.name} was just taken by someone else.") from exc
    return pick


def release_player_picks(session: Session, player_id: int) -> int:
    """Return a player's teams to the pool (e.g. on deposit rejection). Count released."""
    n = session.query(Pick).filter(Pick.player_id == player_id).delete()
    session.flush()
    return n
