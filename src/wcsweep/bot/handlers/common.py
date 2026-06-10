"""Shared handler helpers: session access, registration, admin guard."""

from __future__ import annotations

from functools import wraps
from typing import Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

from ...db import session_scope
from ...models import Player
from ...services import players as players_svc

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


def esc(text: object) -> str:
    """Escape user-provided text for legacy Markdown (names, usernames, etc.).

    Telegram chokes on unescaped _ * ` [ in display names/usernames (e.g. @john_doe),
    so any interpolated user content must pass through here."""
    return escape_markdown(str(text), version=1)


def _display_name(update: Update) -> str:
    u = update.effective_user
    if u is None:
        return "Unknown"
    return (u.full_name or u.username or str(u.id)).strip()


async def notify(context: ContextTypes.DEFAULT_TYPE, telegram_id: int, text: str, **kwargs) -> bool:
    """Best-effort DM to a player; returns False if they've never started the bot."""
    try:
        await context.bot.send_message(chat_id=telegram_id, text=text, **kwargs)
        return True
    except Exception:
        return False


async def reply(update: Update, text: str, **kwargs) -> None:
    """Reply whether the update is a message or a callback query."""
    if update.callback_query is not None and update.callback_query.message is not None:
        await update.callback_query.message.reply_text(text, **kwargs)
    elif update.effective_message is not None:
        await update.effective_message.reply_text(text, **kwargs)


def ensure_registered(func: Handler) -> Handler:
    """Decorator: get-or-create the player row, attach it as context.user_data['player_id']."""

    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user is None:
            return
        with session_scope() as session:
            player, _ = players_svc.register(
                session, user.id, user.username, _display_name(update)
            )
            context.user_data["player_id"] = player.id
            context.user_data["is_admin"] = player.is_admin
        await func(update, context)

    return wrapper


def admin_only(func: Handler) -> Handler:
    """Decorator: reject non-admins."""

    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user is None:
            return
        with session_scope() as session:
            if not players_svc.is_admin(session, user.id):
                await reply(update, "⛔ Admins only.")
                return
        await func(update, context)

    return wrapper


def current_player(session, update: Update) -> Player | None:
    user = update.effective_user
    if user is None:
        return None
    return players_svc.get_by_telegram_id(session, user.id)
