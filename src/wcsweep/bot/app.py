"""Bot bootstrap: wires handlers and starts polling.

Run with `python -m wcsweep.bot.app` or the `wcsweep-bot` console script.
"""

from __future__ import annotations

import logging
from datetime import time as dt_time
from zoneinfo import ZoneInfo

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
)

from ..config import get_settings
from ..db import init_db
from .handlers import admin, player
from .jobs import poll_job

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s", level=logging.INFO
)
log = logging.getLogger("wcsweep")


def build_application() -> Application:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set (see .env.example).")

    init_db()
    app = Application.builder().token(settings.telegram_bot_token).build()

    # Player commands
    app.add_handler(CommandHandler("start", player.start))
    app.add_handler(CommandHandler("help", player.help_cmd))
    app.add_handler(CommandHandler("deposit", player.deposit))
    app.add_handler(CommandHandler("mypicks", player.mypicks))
    app.add_handler(CommandHandler("leaderboard", player.leaderboard))
    app.add_handler(CommandHandler("teams", player.teams))
    app.add_handler(CommandHandler("fixtures", player.fixtures))
    app.add_handler(CommandHandler("draft", player.draft_status))
    app.add_handler(CommandHandler("pick", player.pick))

    # Admin commands
    app.add_handler(CommandHandler("admin", admin.admin_menu))
    app.add_handler(CommandHandler("list", admin.list_players))
    app.add_handler(CommandHandler("pending", admin.pending))
    app.add_handler(CommandHandler("approve", admin.approve))
    app.add_handler(CommandHandler("reject", admin.reject))
    app.add_handler(CommandHandler("openreg", admin.openreg))
    app.add_handler(CommandHandler("closereg", admin.closereg))
    app.add_handler(CommandHandler("startdraft", admin.startdraft))
    app.add_handler(CommandHandler("skipturn", admin.skipturn))
    app.add_handler(CommandHandler("lockpicks", admin.lockpicks))
    app.add_handler(CommandHandler("addmatch", admin.addmatch))
    app.add_handler(CommandHandler("matches", admin.matches))
    app.add_handler(CommandHandler("setresult", admin.setresult))
    app.add_handler(CommandHandler("recompute", admin.recompute))
    app.add_handler(CommandHandler("sync", admin.sync))
    app.add_handler(CommandHandler("mapteam", admin.mapteam))
    app.add_handler(CommandHandler("endgame", admin.endgame))
    app.add_handler(CommandHandler("broadcast", admin.broadcast))

    # Callback queries
    app.add_handler(CallbackQueryHandler(admin.deposit_callback, pattern=r"^dep:"))

    # Scheduled daily results poll (only if a provider is configured).
    if settings.football_data_api_key and app.job_queue is not None:
        run_at = _parse_daily_time(settings.results_poll_time, settings.results_poll_tz)
        app.job_queue.run_daily(poll_job, time=run_at, name="results-poll")
        log.info(
            "Daily results poll scheduled at %s %s",
            settings.results_poll_time,
            settings.results_poll_tz,
        )
    else:
        log.info("Results polling disabled (no FOOTBALL_DATA_API_KEY).")

    return app


def _parse_daily_time(hhmm: str, tz_name: str) -> dt_time:
    """Build a tz-aware datetime.time from 'HH:MM' + an IANA timezone name."""
    hour, minute = (int(x) for x in hhmm.split(":"))
    return dt_time(hour=hour, minute=minute, tzinfo=ZoneInfo(tz_name))


def main() -> None:
    app = build_application()
    log.info("Starting WC 2026 sweepstake bot…")
    app.run_polling()


if __name__ == "__main__":
    main()
