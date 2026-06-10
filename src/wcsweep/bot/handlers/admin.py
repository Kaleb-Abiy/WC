"""Admin command handlers."""

from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ...config import get_settings
from ...db import session_scope
from ...models import (
    Match,
    MatchStatus,
    Player,
    Stage,
    Team,
)
from ...services import deposits as deposits_svc
from ...services import draft as draft_svc
from ...services import game as game_svc
from ...services import players as players_svc
from ...services import scoring
from .. import keyboards
from .common import admin_only, current_player, esc, notify, reply

ADMIN_HELP = (
    "*Admin commands*\n"
    "/list — all players with their ids & status\n"
    "/pending — review deposits\n"
    "/approve <@user|id> · /reject <@user|id> <reason>\n"
    "/openreg · /closereg\n"
    "/startdraft — shuffle players & open the random draw\n"
    "/skipturn — auto-draw for the player on the clock\n"
    "/lockpicks — end draw & lock\n"
    "/addmatch <home> <away> [stage] — create a fixture\n"
    "/matches — list fixtures with ids\n"
    "/setresult <match_id> <h> <a> [pen:<team>] — record/override\n"
    "/sync — pull results from the API now\n"
    "/mapteam <team> <api_id> — bind a team to its API id\n"
    "/recompute — rebuild score ledger\n"
    "/endgame [force] — declare the winner\n"
    "/broadcast <msg> — message all active players"
)


def _resolve_team(session, token: str) -> Team | None:
    from sqlalchemy import func, select

    token = token.strip()
    if token.isdigit():
        t = session.get(Team, int(token))
        if t:
            return t
    return session.scalar(
        select(Team).where(func.lower(Team.name) == token.lower())
    )


@admin_only
async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply(update, ADMIN_HELP, parse_mode=ParseMode.MARKDOWN)


_STATUS_ICON = {
    "PENDING_DEPOSIT": "⬜",
    "DEPOSIT_SUBMITTED": "💵",
    "ACTIVE": "✅",
    "LOCKED": "🔒",
    "REJECTED": "❌",
}


@admin_only
async def list_players(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show every player with the id to use in /approve, /reject etc."""
    from sqlalchemy import func, select

    from ...models import Pick

    with session_scope() as session:
        players = list(session.scalars(select(Player).order_by(Player.id)))
        if not players:
            await reply(update, "No players registered yet.")
            return
        pick_counts = dict(
            session.execute(
                select(Pick.player_id, func.count(Pick.id)).group_by(Pick.player_id)
            ).all()
        )
        lines = ["*Players* — id · status · name (picks)"]
        for p in players:
            icon = _STATUS_ICON.get(p.status.value, "•")
            handle = f"@{esc(p.username)}" if p.username else "—"
            n = pick_counts.get(p.id, 0)
            admin_tag = " 👑" if p.is_admin else ""
            lines.append(
                f"`{p.id}` {icon} {esc(p.display_name)} ({handle}) — {p.status.value} · {n} pick(s){admin_tag}"
            )
        lines.append("\nApprove with `/approve <id>` (or `/approve @username`).")
    await reply(update, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@admin_only
async def pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with session_scope() as session:
        items = deposits_svc.pending(session)
        if not items:
            await reply(update, "No deposits awaiting review. 🎉")
            return
        payloads = [
            (d.player.display_name, d.player.username, d.note, d.player_id)
            for d in items
        ]
    for name, username, note, pid in payloads:
        handle = f"@{esc(username)}" if username else f"id {pid}"
        note_txt = f"\nNote: {esc(note)}" if note else ""
        await reply(
            update,
            f"💵 *{esc(name)}* ({handle}) submitted a deposit.{note_txt}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboards.deposit_review(pid),
        )


@admin_only
async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await reply(update, "Usage: /approve <@user|id>")
        return
    with session_scope() as session:
        admin = current_player(session, update)
        target = players_svc.resolve_target(session, context.args[0])
        if target is None:
            await reply(update, "Couldn't find that player.")
            return
        try:
            deposits_svc.approve(session, target, admin)
        except deposits_svc.DepositError as exc:
            await reply(update, f"⚠️ {exc}")
            return
        name, tid = target.display_name, target.telegram_id
    await reply(update, f"✅ Approved *{esc(name)}* — they can now draft.", parse_mode=ParseMode.MARKDOWN)
    await notify(context, tid, "✅ Your deposit was approved! You're in the draft.")


@admin_only
async def reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await reply(update, "Usage: /reject <@user|id> <reason>")
        return
    reason = " ".join(context.args[1:])
    with session_scope() as session:
        admin = current_player(session, update)
        target = players_svc.resolve_target(session, context.args[0])
        if target is None:
            await reply(update, "Couldn't find that player.")
            return
        try:
            deposits_svc.reject(session, target, admin, reason)
        except deposits_svc.DepositError as exc:
            await reply(update, f"⚠️ {exc}")
            return
        name, tid = target.display_name, target.telegram_id
    await reply(update, f"❌ Rejected *{esc(name)}*.", parse_mode=ParseMode.MARKDOWN)
    await notify(context, tid, f"❌ Your deposit was rejected: {reason}")


async def deposit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline 'dep:approve:<pid>' / 'dep:reject:<pid>'."""
    query = update.callback_query
    _, action, pid_s = query.data.split(":")
    pid = int(pid_s)
    with session_scope() as session:
        if not players_svc.is_admin(session, update.effective_user.id):
            await query.answer("Admins only.", show_alert=True)
            return
        admin = players_svc.get_by_telegram_id(session, update.effective_user.id)
        target = session.get(Player, pid)
        if target is None:
            await query.answer("Player not found.", show_alert=True)
            return
        try:
            if action == "approve":
                deposits_svc.approve(session, target, admin)
                verb, note = "approved", "✅ Your deposit was approved! You're in the draft."
            else:
                deposits_svc.reject(session, target, admin, "Rejected by admin")
                verb, note = "rejected", "❌ Your deposit was rejected. Contact the admin."
        except deposits_svc.DepositError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        name, tid = target.display_name, target.telegram_id
    await query.answer(f"{verb.capitalize()}!")
    await query.edit_message_text(f"{'✅' if action == 'approve' else '❌'} {name} {verb}.")
    await notify(context, tid, note)


@admin_only
async def openreg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with session_scope() as session:
        game_svc.open_registration(session, True)
    await reply(update, "📝 Registration is now OPEN.")


@admin_only
async def closereg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with session_scope() as session:
        game_svc.open_registration(session, False)
    await reply(update, "🔒 Registration is now CLOSED.")


@admin_only
async def startdraft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    seed = context.args[0] if context.args else None
    with session_scope() as session:
        try:
            draft = draft_svc.start(session, seed=seed)
        except draft_svc.DraftError as exc:
            await reply(update, f"⚠️ {exc}")
            return
        order = [esc(session.get(Player, pid).display_name) for pid in draft.order_player_ids]
        first_id = draft_svc.current_player_id(session)
        first = session.get(Player, first_id)
        first_name, first_tid, used_seed = esc(first.display_name), first.telegram_id, draft.seed
    await reply(
        update,
        "🎲 *Draft started!*\nOrder: " + " → ".join(order) +
        f"\nFirst up: *{first_name}*\n_seed: `{used_seed}`_",
        parse_mode=ParseMode.MARKDOWN,
    )
    await notify(context, first_tid, "🎲 You're first — run /pick to draw your teams!")


@admin_only
async def skipturn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with session_scope() as session:
        try:
            result = draft_svc.auto_pick(session)
        except draft_svc.DraftError as exc:
            await reply(update, f"⚠️ {exc}")
            return
        if result is None:
            await reply(update, "No active turn to skip.")
            return
        pid, teams = result
        p = session.get(Player, pid)
        team_names = ", ".join(esc(t.name) for t in teams)
        nxt_id = draft_svc.current_player_id(session)
        nxt = session.get(Player, nxt_id) if nxt_id else None
        info = (p.display_name, team_names, nxt.display_name if nxt else None,
                nxt.telegram_id if nxt else None)
    name, team_names, nxt_name, nxt_tid = info
    msg = f"⏭️ Auto-drew {team_names} for *{esc(name)}*."
    if nxt_name:
        msg += f"\nNext: *{esc(nxt_name)}*"
        await notify(context, nxt_tid, "🎲 It's your turn — run /pick to draw your teams!")
    else:
        msg += "\n🏁 Draft complete."
    await reply(update, msg, parse_mode=ParseMode.MARKDOWN)


@admin_only
async def lockpicks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with session_scope() as session:
        game_svc.lock_picks(session)
    await reply(update, "🔒 Picks locked. Game is RUNNING.")


@admin_only
async def addmatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await reply(update, "Usage: /addmatch <home> <away> [stage]\nStages: GROUP R32 R16 QF SF THIRD FINAL")
        return
    home_tok, away_tok = context.args[0], context.args[1]
    stage_tok = context.args[2].upper() if len(context.args) > 2 else "GROUP"
    try:
        stage = Stage(stage_tok)
    except ValueError:
        await reply(update, f"Unknown stage '{stage_tok}'. Use GROUP R32 R16 QF SF THIRD FINAL.")
        return
    with session_scope() as session:
        home = _resolve_team(session, home_tok)
        away = _resolve_team(session, away_tok)
        if not home or not away:
            await reply(update, "Couldn't resolve one of the teams (use exact name or team id).")
            return
        m = Match(home_team_id=home.id, away_team_id=away.id, stage=stage)
        session.add(m)
        session.flush()
        mid, hn, an = m.id, home.name, away.name
    await reply(update, f"➕ Match #{mid}: {hn} vs {an} [{stage.value}]")


@admin_only
async def matches(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from sqlalchemy import select

    with session_scope() as session:
        rows = list(session.scalars(select(Match).order_by(Match.id)))
        if not rows:
            await reply(update, "No matches yet. Add one with /addmatch.")
            return
        lines = ["*Matches*"]
        for m in rows:
            h, a = esc(m.home_team.name), esc(m.away_team.name)
            if m.status == MatchStatus.FINISHED:
                lines.append(f"#{m.id} ✅ {h} {m.home_score}–{m.away_score} {a} [{m.stage.value}]")
            else:
                lines.append(f"#{m.id} 🕒 {h} vs {a} [{m.stage.value}]")
    await reply(update, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@admin_only
async def setresult(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 3:
        await reply(update, "Usage: /setresult <match_id> <home_score> <away_score> [pen:<team>]")
        return
    try:
        match_id = int(context.args[0])
        hs = int(context.args[1])
        as_ = int(context.args[2])
    except ValueError:
        await reply(update, "match_id and scores must be numbers.")
        return
    pen_token = None
    for a in context.args[3:]:
        if a.lower().startswith("pen:"):
            pen_token = a.split(":", 1)[1]
    with session_scope() as session:
        m = session.get(Match, match_id)
        if m is None:
            await reply(update, f"No match #{match_id}.")
            return
        pen_id = None
        if pen_token:
            pt = _resolve_team(session, pen_token)
            if pt is None:
                await reply(update, f"Couldn't resolve penalty winner '{pen_token}'.")
                return
            pen_id = pt.id
        try:
            game_svc.set_result(session, m, hs, as_, pen_winner_team_id=pen_id)
        except game_svc.GameError as exc:
            await reply(update, f"⚠️ {exc}")
            return
        game_svc.mark_eliminations(session)
        h, a = m.home_team.name, m.away_team.name
    await reply(update, f"✅ Recorded: {esc(h)} {hs}–{as_} {esc(a)}. Ledger rebuilt.", parse_mode=ParseMode.MARKDOWN)
    from ..jobs import maybe_finish_game

    if await maybe_finish_game(context):
        await reply(update, "🏁 That result ended the game — winner declared.")


@admin_only
async def recompute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with session_scope() as session:
        n = scoring.rebuild_ledger(session)
        game_svc.mark_eliminations(session)
    await reply(update, f"♻️ Ledger rebuilt: {n} score events.")


@admin_only
async def sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Force a results pull from the API right now."""
    from ..jobs import run_sync

    await reply(update, "🔄 Syncing results from the API…")
    try:
        report = await run_sync(context)
    except Exception as exc:  # noqa: BLE001 — surface API errors to the admin
        await reply(update, f"⚠️ Sync failed: {exc}")
        return
    if report is None:
        await reply(update, "No results provider configured (set FOOTBALL_DATA_API_KEY).")
        return
    lines = [
        "✅ Sync done.",
        f"Created: {report.created} · Updated: {report.updated} · "
        f"Newly finished: {len(report.newly_finished)} · Skipped (manual): {report.skipped_manual}",
    ]
    if report.pending_fixtures:
        lines.append(
            f"⏳ {report.pending_fixtures} fixture(s) waiting on the draw (teams not decided yet)."
        )
    if report.unmatched_teams:
        shown = "\n".join(
            f"  • {esc(name)} → `/mapteam <our_team> {api_id}`"
            for name, api_id in report.unmatched_teams[:15]
        )
        lines.append(
            f"\n⚠️ {len(report.unmatched_teams)} team name(s) didn't match ours — "
            f"copy the api id into /mapteam:\n{shown}"
        )
    await reply(update, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@admin_only
async def mapteam(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bind one of our teams to its football-data api id (fixes name-match failures)."""
    if len(context.args) < 2 or not context.args[-1].isdigit():
        await reply(update, "Usage: /mapteam <our_team_name_or_id> <api_team_id>")
        return
    from sqlalchemy import select

    api_id = int(context.args[-1])
    token = " ".join(context.args[:-1])
    with session_scope() as session:
        team = _resolve_team(session, token)
        if team is None:
            await reply(update, f"Couldn't find team '{token}'.")
            return
        clash = session.scalar(select(Team).where(Team.api_team_id == api_id))
        if clash is not None and clash.id != team.id:
            await reply(update, f"⚠️ api id {api_id} is already mapped to {esc(clash.name)}.")
            return
        team.api_team_id = api_id
        name = team.name
    await reply(update, f"🔗 Mapped *{esc(name)}* → api id `{api_id}`.", parse_mode=ParseMode.MARKDOWN)


@admin_only
async def endgame(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    force = bool(context.args and context.args[0].lower() == "force")
    with session_scope() as session:
        try:
            winner = game_svc.declare_winner(session, force=force)
        except game_svc.GameError as exc:
            await reply(update, f"⚠️ {exc} (use /endgame force to override)")
            return
        if winner is None:
            await reply(update, "No standings to resolve yet.")
            return
        name = winner.display_name
        pot = deposits_svc.pot_total(session)
        currency = get_settings().currency
    await reply(
        update,
        f"🎉 *Game over!* Winner: *{esc(name)}* takes the pot of {pot:g} {currency}!",
        parse_mode=ParseMode.MARKDOWN,
    )


@admin_only
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await reply(update, "Usage: /broadcast <message>")
        return
    msg = " ".join(context.args)
    with session_scope() as session:
        targets = [p.telegram_id for p in players_svc.active_players(session)]
    sent = 0
    for tid in targets:
        if await notify(context, tid, f"📢 {msg}"):
            sent += 1
    await reply(update, f"Sent to {sent}/{len(targets)} players.")
