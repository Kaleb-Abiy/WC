"""Player registration and lookup."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Player, PlayerStatus


def get_by_telegram_id(session: Session, telegram_id: int) -> Player | None:
    return session.scalar(
        select(Player).where(Player.telegram_id == telegram_id)
    )


def register(
    session: Session, telegram_id: int, username: str | None, display_name: str
) -> tuple[Player, bool]:
    """Get-or-create a player. Returns (player, created)."""
    player = get_by_telegram_id(session, telegram_id)
    if player is not None:
        # Keep username/display fresh.
        player.username = username
        player.display_name = display_name
        return player, False

    is_admin = telegram_id in get_settings().admin_telegram_ids
    player = Player(
        telegram_id=telegram_id,
        username=username,
        display_name=display_name,
        status=PlayerStatus.PENDING_DEPOSIT,
        is_admin=is_admin,
    )
    session.add(player)
    session.flush()
    return player, True


def is_admin(session: Session, telegram_id: int) -> bool:
    if telegram_id in get_settings().admin_telegram_ids:
        return True
    player = get_by_telegram_id(session, telegram_id)
    return bool(player and player.is_admin)


def active_players(session: Session) -> list[Player]:
    return list(
        session.scalars(
            select(Player).where(
                Player.status.in_([PlayerStatus.ACTIVE, PlayerStatus.LOCKED])
            )
        )
    )


def resolve_target(session: Session, token: str) -> Player | None:
    """Resolve an admin command target by @username, numeric telegram_id, or db id."""
    token = token.strip().lstrip("@")
    if not token:
        return None
    if token.isdigit():
        n = int(token)
        return session.scalar(
            select(Player).where(
                (Player.telegram_id == n) | (Player.id == n)
            )
        )
    return session.scalar(select(Player).where(Player.username == token))
