"""Deposit submission and admin approval.

Money moves out-of-band; the bot only tracks intent + admin approval. A player
becomes ACTIVE (eligible to draft) only once a deposit is APPROVED.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Deposit, DepositStatus, Player, PlayerStatus, utcnow


class DepositError(Exception):
    pass


def submit(
    session: Session,
    player: Player,
    note: str | None = None,
    proof_file_id: str | None = None,
) -> Deposit:
    if player.status == PlayerStatus.ACTIVE:
        raise DepositError("You're already approved and active.")

    settings = get_settings()
    deposit = Deposit(
        player_id=player.id,
        amount=settings.entry_amount,
        currency=settings.currency,
        note=note,
        proof_file_id=proof_file_id,
        status=DepositStatus.SUBMITTED,
    )
    session.add(deposit)
    player.status = PlayerStatus.DEPOSIT_SUBMITTED
    session.flush()
    return deposit


def pending(session: Session) -> list[Deposit]:
    return list(
        session.scalars(
            select(Deposit)
            .where(Deposit.status == DepositStatus.SUBMITTED)
            .order_by(Deposit.created_at)
        )
    )


def _latest_for_player(session: Session, player_id: int) -> Deposit | None:
    return session.scalar(
        select(Deposit)
        .where(Deposit.player_id == player_id)
        .order_by(Deposit.created_at.desc())
    )


def approve(session: Session, target: Player, admin: Player) -> Deposit:
    deposit = _latest_for_player(session, target.id)
    if deposit is None:
        raise DepositError(f"{target.display_name} has no deposit on record.")
    deposit.status = DepositStatus.APPROVED
    deposit.reviewed_by = admin.id
    deposit.reviewed_at = utcnow()
    target.status = PlayerStatus.ACTIVE
    session.flush()
    return deposit


def reject(session: Session, target: Player, admin: Player, reason: str) -> Deposit:
    deposit = _latest_for_player(session, target.id)
    if deposit is None:
        raise DepositError(f"{target.display_name} has no deposit on record.")
    deposit.status = DepositStatus.REJECTED
    deposit.reviewed_by = admin.id
    deposit.review_note = reason
    deposit.reviewed_at = utcnow()
    target.status = PlayerStatus.PENDING_DEPOSIT
    session.flush()
    return deposit


def approved_player_count(session: Session) -> int:
    return len(
        list(
            session.scalars(
                select(Player.id).where(
                    Player.status.in_([PlayerStatus.ACTIVE, PlayerStatus.LOCKED])
                )
            )
        )
    )


def pot_total(session: Session) -> float:
    """Winner-takes-all pot = entry amount x number of approved (active/locked) players."""
    return get_settings().entry_amount * approved_player_count(session)
