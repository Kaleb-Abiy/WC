"""Scheduled results polling + the shared sync routine used by /sync.

Telegram lives here (not in services): we fetch via the provider, hand matches to the
pure `ingest_matches`, then announce any newly-finished matches to the group chat.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..config import get_settings
from ..db import session_scope
from ..models import GamePhase, Match, Pick, Player
from ..services import deposits as deposits_svc
from ..services import game as game_svc
from ..services import results as results_svc
from .handlers.common import esc, notify

log = logging.getLogger("wcsweep.jobs")


def _format_finished(session, match_id: int) -> str:
    """Build a 'FT' announcement line for a finished match, naming affected players."""
    m = session.get(Match, match_id)
    h, a = m.home_team, m.away_team
    line = f"⚽ *FT* — {esc(h.name)} {m.home_score}–{m.away_score} {esc(a.name)} [{m.stage.value}]"
    if m.decided_by_pens and m.pen_winner_team_id:
        winner = session.get(type(h), m.pen_winner_team_id)
        line += f" (pens: {esc(winner.name)})"

    owners = {
        pk.team_id: session.get(Player, pk.player_id)
        for pk in session.scalars(
            select(Pick).where(Pick.team_id.in_([h.id, a.id]))
        )
    }
    tags = [
        f"{esc(owners[t.id].display_name)} ({esc(t.name)})"
        for t in (h, a)
        if t.id in owners
    ]
    if tags:
        line += "\n   👤 " + ", ".join(tags)
    return line


async def run_sync(context: ContextTypes.DEFAULT_TYPE) -> results_svc.SyncReport | None:
    """Fetch from the provider and reconcile. Returns the report, or None if no provider.

    Announces newly-finished matches to GROUP_CHAT_ID when configured.
    """
    settings = get_settings()
    provider = results_svc.build_provider(settings)
    if provider is None:
        return None

    # Provider HTTP is synchronous; keep the event loop free.
    matches = await asyncio.to_thread(provider.fetch_matches)

    announcements: list[str] = []
    with session_scope() as session:
        report = results_svc.ingest_matches(session, matches)
        if settings.group_chat_id and report.newly_finished:
            announcements = [
                _format_finished(session, mid) for mid in report.newly_finished
            ]

    for text in announcements:
        await notify(context, settings.group_chat_id, text, parse_mode=ParseMode.MARKDOWN)
    if report.changed:
        await maybe_finish_game(context)
    return report


async def maybe_finish_game(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """If the game is RUNNING and no relevant fixture can change standings, declare the
    winner and announce it. Idempotent — does nothing once FINISHED. Returns True if it
    just ended the game."""
    settings = get_settings()
    text = None
    with session_scope() as session:
        state = game_svc.get_state(session)
        if state.phase != GamePhase.RUNNING or not game_svc.can_end(session):
            return False
        winner = game_svc.declare_winner(session)  # safe: can_end() is True
        if winner is None:
            return False
        pot = deposits_svc.pot_total(session)
        text = (
            f"🏁 *Full time on the sweepstake!*\n"
            f"🎉 Winner: *{esc(winner.display_name)}* — takes the pot of "
            f"{pot:g} {settings.currency}!\nThanks for playing. 🍻"
        )
    if text and settings.group_chat_id:
        await notify(context, settings.group_chat_id, text, parse_mode=ParseMode.MARKDOWN)
    return True


async def poll_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback: poll results on an interval."""
    try:
        report = await run_sync(context)
    except Exception:  # never let a transient API error kill the job
        log.exception("results poll failed")
        return
    if report and report.changed:
        log.info(
            "sync: %d created, %d updated, %d newly finished",
            report.created,
            report.updated,
            len(report.newly_finished),
        )
