"""SQLAlchemy ORM models for the sweepstake.

The design centres on an immutable `score_events` ledger: the leaderboard is always
a query over it, so recompute is deterministic and auditable.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- enums


class PlayerStatus(str, enum.Enum):
    PENDING_DEPOSIT = "PENDING_DEPOSIT"
    DEPOSIT_SUBMITTED = "DEPOSIT_SUBMITTED"
    ACTIVE = "ACTIVE"
    LOCKED = "LOCKED"
    REJECTED = "REJECTED"


class DepositStatus(str, enum.Enum):
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class Stage(str, enum.Enum):
    GROUP = "GROUP"
    R32 = "R32"
    R16 = "R16"
    QF = "QF"
    SF = "SF"
    THIRD = "THIRD"
    FINAL = "FINAL"


class MatchStatus(str, enum.Enum):
    SCHEDULED = "SCHEDULED"
    LIVE = "LIVE"
    FINISHED = "FINISHED"


class ResultSource(str, enum.Enum):
    API = "API"
    MANUAL = "MANUAL"


class Outcome(str, enum.Enum):
    WIN = "WIN"
    DRAW = "DRAW"
    LOSS = "LOSS"


class GamePhase(str, enum.Enum):
    SETUP = "SETUP"
    REGISTRATION = "REGISTRATION"
    PICKING = "PICKING"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"


class DraftStatus(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"


# --------------------------------------------------------------------------- models


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[PlayerStatus] = mapped_column(
        Enum(PlayerStatus), default=PlayerStatus.PENDING_DEPOSIT, nullable=False
    )
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    deposits: Mapped[list["Deposit"]] = relationship(
        back_populates="player", foreign_keys="Deposit.player_id"
    )
    picks: Mapped[list["Pick"]] = relationship(
        back_populates="player", foreign_keys="Pick.player_id"
    )


class Deposit(Base):
    __tablename__ = "deposits"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    amount: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    note: Mapped[str | None] = mapped_column(Text)
    proof_file_id: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[DepositStatus] = mapped_column(
        Enum(DepositStatus), default=DepositStatus.SUBMITTED, nullable=False
    )
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)

    player: Mapped[Player] = relationship(
        back_populates="deposits", foreign_keys=[player_id]
    )


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    api_team_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    group_letter: Mapped[str | None] = mapped_column(String(2))
    flag_emoji: Mapped[str | None] = mapped_column(String(8))
    eliminated: Mapped[bool] = mapped_column(Boolean, default=False)


class Pick(Base):
    __tablename__ = "picks"
    __table_args__ = (
        UniqueConstraint("team_id", name="uq_pick_team"),  # one team -> one player
        # Upper bound (teams_per_player) is config, enforced in picks.assign.
        CheckConstraint("pick_order >= 1", name="ck_pick_order"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    pick_order: Mapped[int] = mapped_column(Integer)  # 1..teams_per_player
    round_no: Mapped[int] = mapped_column(Integer, default=1)  # slot index of the draw
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    player: Mapped[Player] = relationship(
        back_populates="picks", foreign_keys=[player_id]
    )
    team: Mapped[Team] = relationship()


class Draft(Base):
    """Singleton (id=1) holding the randomized snake-draft state."""

    __tablename__ = "draft"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    seed: Mapped[str | None] = mapped_column(String(64))
    order_player_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    current_index: Mapped[int] = mapped_column(Integer, default=0)  # pos in snake seq
    current_round: Mapped[int] = mapped_column(Integer, default=1)
    turn_deadline_utc: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[DraftStatus] = mapped_column(
        Enum(DraftStatus), default=DraftStatus.NOT_STARTED, nullable=False
    )


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    api_match_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    stage: Mapped[Stage] = mapped_column(Enum(Stage), default=Stage.GROUP)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    kickoff_utc: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[MatchStatus] = mapped_column(
        Enum(MatchStatus), default=MatchStatus.SCHEDULED, nullable=False
    )
    home_score: Mapped[int | None] = mapped_column(Integer)  # after extra time
    away_score: Mapped[int | None] = mapped_column(Integer)
    decided_by_pens: Mapped[bool] = mapped_column(Boolean, default=False)
    pen_winner_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    source: Mapped[ResultSource] = mapped_column(
        Enum(ResultSource), default=ResultSource.API
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime)

    home_team: Mapped[Team] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped[Team] = relationship(foreign_keys=[away_team_id])


class ScoreEvent(Base):
    """Immutable ledger: exactly one row per (team, match). Drives the leaderboard."""

    __tablename__ = "score_events"
    __table_args__ = (
        UniqueConstraint("team_id", "match_id", name="uq_scoreevent_team_match"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    outcome: Mapped[Outcome] = mapped_column(Enum(Outcome))
    points: Mapped[int] = mapped_column(Integer)
    goals_for: Mapped[int] = mapped_column(Integer, default=0)
    goals_against: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class GameState(Base):
    """Singleton (id=1) holding global game phase + winner."""

    __tablename__ = "game_state"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    phase: Mapped[GamePhase] = mapped_column(
        Enum(GamePhase), default=GamePhase.SETUP, nullable=False
    )
    registration_open: Mapped[bool] = mapped_column(Boolean, default=False)
    picking_open: Mapped[bool] = mapped_column(Boolean, default=False)
    picks_locked_at: Mapped[datetime | None] = mapped_column(DateTime)
    winner_player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    tiebreak_seed: Mapped[str | None] = mapped_column(String(64))
