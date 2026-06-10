"""Inline keyboards."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def deposit_review(player_id: int) -> InlineKeyboardMarkup:
    """Approve / reject buttons for an admin reviewing a deposit."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"dep:approve:{player_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"dep:reject:{player_id}"),
            ]
        ]
    )
