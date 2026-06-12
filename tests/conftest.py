"""Test fixtures: an in-memory SQLite session with all tables created."""

from __future__ import annotations

import os

# Pin the allocation size the suite was written against, regardless of the
# developer's .env (env vars beat .env in pydantic-settings). Individual tests
# that need a different value monkeypatch + cache-clear get_settings.
os.environ["TEAMS_PER_PLAYER"] = "2"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from wcsweep.config import get_settings
from wcsweep.models import Base

get_settings.cache_clear()


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, future=True
    )
    # Enable FK enforcement so UNIQUE(team_id) etc. behave like prod.
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _fk(conn, _):  # noqa: ANN001
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def make_player(session):
    from wcsweep.models import Player, PlayerStatus

    counter = {"n": 0}

    def _make(name: str, status: PlayerStatus = PlayerStatus.ACTIVE) -> Player:
        counter["n"] += 1
        p = Player(
            telegram_id=1000 + counter["n"],
            username=name.lower(),
            display_name=name,
            status=status,
        )
        session.add(p)
        session.flush()
        return p

    return _make


@pytest.fixture()
def make_team(session):
    from wcsweep.models import Team

    def _make(name: str, group: str = "A") -> Team:
        t = Team(name=name, group_letter=group)
        session.add(t)
        session.flush()
        return t

    return _make
