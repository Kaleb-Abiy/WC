"""Database engine and session management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

_settings = get_settings()

# check_same_thread=False lets the async bot's threads share the SQLite connection.
_connect_args = (
    {"check_same_thread": False}
    if _settings.database_url.startswith("sqlite")
    else {}
)

engine: Engine = create_engine(
    _settings.database_url,
    connect_args=_connect_args,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@event.listens_for(engine, "connect")
def _enable_sqlite_fk(dbapi_conn, _record):  # noqa: ANN001
    """SQLite ignores FK constraints unless explicitly enabled per-connection."""
    if _settings.database_url.startswith("sqlite"):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session: commits on success, rolls back on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create all tables. For the MVP we use create_all instead of migrations."""
    from . import models  # noqa: F401  (ensure models are registered)

    models.Base.metadata.create_all(bind=engine)
    _migrate_pick_order_constraint()
    models.Base.metadata.create_all(bind=engine)  # recreate picks if migration dropped it
    _create_views()


def _migrate_pick_order_constraint() -> None:
    """One-off: widen the old CHECK (pick_order in (1, 2)) to pick_order >= 1.

    SQLite can't alter constraints, so an empty picks table is dropped and recreated
    by the create_all that follows. (Non-empty + old constraint is logged loudly —
    that needs a manual rebuild, but can't happen before a draft has run.)
    """
    with engine.begin() as conn:
        if _settings.database_url.startswith("sqlite"):
            row = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE type='table' AND name='picks'")
            ).fetchone()
            if row is None or "pick_order in (1, 2)" not in (row[0] or ""):
                return
            count = conn.execute(text("SELECT count(*) FROM picks")).scalar()
            if count == 0:
                conn.execute(text("DROP TABLE picks"))
            else:
                import logging

                logging.getLogger("wcsweep.db").error(
                    "picks table has the old pick_order constraint AND data — "
                    "manual migration required before picks beyond #2 will work."
                )
        else:
            conn.execute(
                text("ALTER TABLE picks DROP CONSTRAINT IF EXISTS ck_pick_order")
            )
            conn.execute(
                text(
                    "ALTER TABLE picks ADD CONSTRAINT ck_pick_order "
                    "CHECK (pick_order >= 1)"
                )
            )


# Read-only convenience views for browsing the DB directly (team names instead of ids).
# The base tables stay normalized; these just join for human eyes.
_VIEWS = {
    "v_matches": """
        SELECT m.id, m.api_match_id, m.stage, m.status,
               h.name AS home, m.home_score, m.away_score, a.name AS away,
               m.decided_by_pens, w.name AS pen_winner, m.kickoff_utc, m.source
        FROM matches m
        JOIN teams h ON h.id = m.home_team_id
        JOIN teams a ON a.id = m.away_team_id
        LEFT JOIN teams w ON w.id = m.pen_winner_team_id
    """,
    "v_standings": """
        SELECT p.id AS player_id, p.display_name,
               COALESCE(SUM(se.points), 0) AS points,
               COALESCE(SUM(CASE WHEN se.outcome = 'WIN' THEN 1 ELSE 0 END), 0) AS wins,
               COALESCE(SUM(se.goals_for), 0) AS goals_for,
               COALESCE(SUM(se.goals_against), 0) AS goals_against
        FROM players p
        LEFT JOIN score_events se ON se.player_id = p.id
        WHERE p.status IN ('ACTIVE', 'LOCKED')
        GROUP BY p.id, p.display_name
    """,
}


def _create_views() -> None:
    is_sqlite = _settings.database_url.startswith("sqlite")
    with engine.begin() as conn:
        for name, body in _VIEWS.items():
            if is_sqlite:
                conn.execute(text(f"CREATE VIEW IF NOT EXISTS {name} AS {body}"))
            else:
                conn.execute(text(f"DROP VIEW IF EXISTS {name} CASCADE"))
                conn.execute(text(f"CREATE VIEW {name} AS {body}"))
