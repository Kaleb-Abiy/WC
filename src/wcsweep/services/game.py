"""Global game state, results entry/override, and winner resolution."""

from __future__ import annotations

import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    GamePhase,
    GameState,
    Match,
    MatchStatus,
    Player,
    PlayerStatus,
    ResultSource,
    Team,
    utcnow,
)
from . import scoring


class GameError(Exception):
    pass


def get_state(session: Session) -> GameState:
    state = session.get(GameState, 1)
    if state is None:
        state = GameState(id=1, phase=GamePhase.SETUP)
        session.add(state)
        session.flush()
    return state


# ------------------------------------------------------------------ phase toggles


def open_registration(session: Session, value: bool) -> GameState:
    state = get_state(session)
    state.registration_open = value
    if value and state.phase == GamePhase.SETUP:
        state.phase = GamePhase.REGISTRATION
    session.flush()
    return state


def lock_picks(session: Session) -> GameState:
    """Lock all picks and move the game into RUNNING."""
    state = get_state(session)
    state.picking_open = False
    state.picks_locked_at = utcnow()
    state.phase = GamePhase.RUNNING
    session.query(Player).filter(
        Player.status == PlayerStatus.ACTIVE
    ).update({Player.status: PlayerStatus.LOCKED})
    session.flush()
    return state


# ------------------------------------------------------------------ results


def set_result(
    session: Session,
    match: Match,
    home_score: int,
    away_score: int,
    pen_winner_team_id: int | None = None,
    source: ResultSource = ResultSource.MANUAL,
) -> Match:
    """Record/override a match result, then rebuild the score ledger.

    `pen_winner_team_id` is only meaningful when the after-extra-time score is level.
    """
    if home_score < 0 or away_score < 0:
        raise GameError("Scores must be non-negative.")

    decided_by_pens = home_score == away_score and pen_winner_team_id is not None
    if decided_by_pens and pen_winner_team_id not in (
        match.home_team_id,
        match.away_team_id,
    ):
        raise GameError("Penalty winner must be one of the two teams.")

    match.home_score = home_score
    match.away_score = away_score
    match.decided_by_pens = decided_by_pens
    match.pen_winner_team_id = pen_winner_team_id if decided_by_pens else None
    match.status = MatchStatus.FINISHED
    match.source = source
    match.last_synced_at = utcnow()
    session.flush()

    scoring.rebuild_ledger(session)
    return match


# ------------------------------------------------------------------ winner / endgame


def remaining_relevant_fixtures(session: Session) -> int:
    """Count not-finished matches that involve at least one owned team.

    If zero, standings can no longer change -> the game can be resolved.
    """
    from ..models import Pick

    owned = set(session.scalars(select(Pick.team_id)))
    if not owned:
        return 0
    count = 0
    for m in session.scalars(
        select(Match).where(Match.status != MatchStatus.FINISHED)
    ):
        if m.home_team_id in owned or m.away_team_id in owned:
            count += 1
    return count


def can_end(session: Session) -> bool:
    return remaining_relevant_fixtures(session) == 0


def declare_winner(session: Session, force: bool = False) -> Player | None:
    """Resolve the winner per the tie-break order in DESIGN §5 and freeze the board.

    Returns the winning Player, or None if there are no standings yet.
    With force=False, refuses to end while relevant fixtures remain.
    """
    state = get_state(session)
    if not force and not can_end(session):
        raise GameError(
            f"{remaining_relevant_fixtures(session)} relevant fixture(s) still to play."
        )

    standings = scoring.leaderboard(session)
    if not standings:
        return None

    # standings is already sorted by (points, wins, goal_diff, goals_for).
    top = standings[0]
    tied = [
        s
        for s in standings
        if (s.points, s.wins, s.goal_diff, s.goals_for)
        == (top.points, top.wins, top.goal_diff, top.goals_for)
    ]
    if len(tied) > 1:
        seed = secrets.token_hex(8)
        state.tiebreak_seed = seed
        import random

        winner_id = random.Random(seed).choice([s.player_id for s in tied])
    else:
        winner_id = top.player_id

    state.winner_player_id = winner_id
    state.phase = GamePhase.FINISHED
    session.flush()
    return session.get(Player, winner_id)


def mark_eliminations(session: Session) -> int:
    """Mark teams with no remaining (non-finished) fixtures as eliminated.

    Returns the number newly marked. Used for /teams display and end-game checks.
    """
    teams = list(session.scalars(select(Team)))
    open_team_ids: set[int] = set()
    for m in session.scalars(
        select(Match).where(Match.status != MatchStatus.FINISHED)
    ):
        open_team_ids.add(m.home_team_id)
        open_team_ids.add(m.away_team_id)

    newly = 0
    for t in teams:
        should = t.id not in open_team_ids
        if should and not t.eliminated:
            t.eliminated = True
            newly += 1
        elif not should and t.eliminated:
            t.eliminated = False
    session.flush()
    return newly
