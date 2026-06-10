"""Randomized team draw.

Teams are *not* chosen — they're drawn at random. Approved players are shuffled into
a random order (recorded `seed`, so the whole draft is reproducible and auditable); then
each player, on their turn, runs /pick and the bot draws their full allocation
(`teams_per_player`) of random teams from the available pool. One turn per player.

The eligible pool is currently every seeded team. The 3-friends / "top-N by ranking"
variant just narrows that pool — see `_eligible_teams` for the seam to extend later.
"""

from __future__ import annotations

import random
import secrets
from datetime import timedelta, timezone

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    Draft,
    DraftStatus,
    GamePhase,
    Player,
    Team,
    utcnow,
)
from . import game, picks
from .players import active_players


class DraftError(Exception):
    pass


def get_draft(session: Session) -> Draft:
    draft = session.get(Draft, 1)
    if draft is None:
        draft = Draft(id=1)
        session.add(draft)
        session.flush()
    return draft


def _set_deadline(draft: Draft) -> None:
    draft.turn_deadline_utc = utcnow() + timedelta(
        seconds=get_settings().draft_turn_seconds
    )


def _eligible_teams(session: Session) -> list[Team]:
    """The pool teams may be drawn from. Currently all available teams.

    Future (small-group variant): restrict to a configured subset, e.g. top-N by world
    ranking. Narrow this and the rest of the draw logic is unchanged.
    """
    return picks.available_teams(session)


def start(session: Session, seed: str | None = None) -> Draft:
    """Shuffle approved players and open the draw. Refuses to restart."""
    draft = get_draft(session)
    if draft.status != DraftStatus.NOT_STARTED:
        raise DraftError("Draft has already been started.")

    players = active_players(session)
    if len(players) < 2:
        raise DraftError("Need at least 2 approved players to start the draft.")

    cap = get_settings().teams_per_player
    needed = len(players) * cap
    pool = len(_eligible_teams(session))
    if pool < needed:
        raise DraftError(
            f"Not enough teams: {len(players)} players × {cap} = {needed} needed, "
            f"but only {pool} available in the pool."
        )

    seed = seed or secrets.token_hex(8)
    order = [p.id for p in players]
    random.Random(seed).shuffle(order)

    draft.seed = seed
    draft.order_player_ids = order
    draft.current_index = 0
    draft.current_round = 1
    draft.status = DraftStatus.RUNNING
    _set_deadline(draft)

    state = game.get_state(session)
    state.phase = GamePhase.PICKING
    state.picking_open = True
    session.flush()
    return draft


def current_player_id(session: Session) -> int | None:
    draft = get_draft(session)
    if draft.status != DraftStatus.RUNNING:
        return None
    order = list(draft.order_player_ids)
    if draft.current_index >= len(order):
        return None
    return order[draft.current_index]


def _draw_and_assign(session: Session, draft: Draft) -> tuple[int, list[Team]]:
    """Draw the current player's full allocation of random teams and advance.

    The draw is seeded from the draft seed + position, so it's deterministic given the
    seed (reproducible / verifiable), yet unpredictable before the draft starts.
    """
    player_id = current_player_id(session)
    if player_id is None:
        raise DraftError("No player is currently on the clock.")

    cap = get_settings().teams_per_player
    rng = random.Random(f"{draft.seed}:draw:{draft.current_index}")
    drawn: list[Team] = []
    for slot in range(cap):
        pool = _eligible_teams(session)
        if not pool:
            raise DraftError("No teams left to draw.")
        team = rng.choice(pool)
        picks.assign(session, player_id, team.id, round_no=slot + 1)
        drawn.append(team)

    draft.current_index += 1
    if draft.current_index >= len(draft.order_player_ids):
        _complete(session, draft)
    else:
        _set_deadline(draft)
    session.flush()
    return player_id, drawn


def _complete(session: Session, draft: Draft) -> None:
    draft.status = DraftStatus.COMPLETE
    draft.turn_deadline_utc = None
    game.lock_picks(session)
    session.flush()


def draw_for_player(session: Session, player_id: int) -> list[Team]:
    """A player draws their random teams on their turn."""
    draft = get_draft(session)
    if draft.status != DraftStatus.RUNNING:
        raise DraftError("The draft is not currently running.")
    if current_player_id(session) != player_id:
        raise DraftError("It's not your turn yet.")
    _, teams = _draw_and_assign(session, draft)
    return teams


def auto_pick(session: Session) -> tuple[int, list[Team]] | None:
    """Draw for whoever is on the clock (missed deadline or admin /skipturn).

    Returns (player_id, teams) or None if there's no active turn.
    """
    draft = get_draft(session)
    if draft.status != DraftStatus.RUNNING:
        return None
    if current_player_id(session) is None:
        return None
    return _draw_and_assign(session, draft)


def is_deadline_passed(session: Session) -> bool:
    draft = get_draft(session)
    if draft.status != DraftStatus.RUNNING or draft.turn_deadline_utc is None:
        return False
    deadline = draft.turn_deadline_utc
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return utcnow() >= deadline


def status_summary(session: Session) -> dict:
    """Structured snapshot for the /draft command."""
    draft = get_draft(session)
    order = list(draft.order_player_ids)
    players = {p.id: p for p in session.query(Player).all()}
    return {
        "status": draft.status,
        "order": [players.get(pid) for pid in order],
        "on_the_clock": players.get(current_player_id(session)),
        "picked_count": draft.current_index,
        "total": len(order),
        "deadline": draft.turn_deadline_utc,
        "seed": draft.seed,
    }
