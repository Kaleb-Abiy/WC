"""Bot command menus (the list Telegram shows when you type '/').

Players see PLAYER_COMMANDS by default; admins additionally see ADMIN_COMMANDS,
scoped to their private chat so non-admins never see admin commands.
"""

from __future__ import annotations

import logging

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
)
from telegram.ext import Application

from ..config import get_settings

log = logging.getLogger("wcsweep.commands")

PLAYER_COMMANDS = [
    BotCommand("start", "Register / see your next step"),
    BotCommand("deposit", "Submit your entry payment"),
    BotCommand("pick", "Draw your random teams (on your turn)"),
    BotCommand("draft", "See the draw order & whose turn it is"),
    BotCommand("mypicks", "Your teams & points"),
    BotCommand("leaderboard", "Standings & pot"),
    BotCommand("teams", "Team availability"),
    BotCommand("fixtures", "Recent & upcoming results"),
    BotCommand("help", "Commands & rules"),
]

ADMIN_ONLY_COMMANDS = [
    BotCommand("admin", "Admin menu"),
    BotCommand("list", "All players with ids & status"),
    BotCommand("pending", "Review pending deposits"),
    BotCommand("approve", "Approve a deposit: /approve <id>"),
    BotCommand("reject", "Reject a deposit: /reject <id> <reason>"),
    BotCommand("openreg", "Open registration"),
    BotCommand("closereg", "Close registration"),
    BotCommand("startdraft", "Shuffle players & open the draw"),
    BotCommand("skipturn", "Auto-draw for the player on the clock"),
    BotCommand("lockpicks", "End the draw & lock picks"),
    BotCommand("addmatch", "Create a fixture: /addmatch <home> <away> [stage]"),
    BotCommand("matches", "List fixtures with ids"),
    BotCommand("setresult", "Record/override: /setresult <id> <h> <a> [pen:<team>]"),
    BotCommand("sync", "Pull results from the API now"),
    BotCommand("mapteam", "Bind a team to its API id: /mapteam <team> <id>"),
    BotCommand("recompute", "Rebuild the score ledger"),
    BotCommand("endgame", "Declare the winner"),
    BotCommand("broadcast", "Message all players: /broadcast <msg>"),
]

# Admins see player commands plus the admin-only ones.
ADMIN_COMMANDS = PLAYER_COMMANDS + ADMIN_ONLY_COMMANDS


async def configure_commands(app: Application) -> None:
    """Register the command menus with Telegram (runs in post_init)."""
    bot = app.bot
    await bot.set_my_commands(PLAYER_COMMANDS, scope=BotCommandScopeDefault())
    for admin_id in get_settings().admin_telegram_ids:
        try:
            await bot.set_my_commands(
                ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id)
            )
        except Exception:  # admin hasn't opened a chat with the bot yet
            log.warning("Couldn't set admin command menu for %s (no chat yet)", admin_id)
    log.info("Command menus configured.")
