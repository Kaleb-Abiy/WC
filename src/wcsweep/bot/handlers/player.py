"""Player-facing command handlers."""

from __future__ import annotations

from sqlalchemy import select
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ...config import get_settings
from ...db import session_scope
from ...models import GamePhase, PlayerStatus
from ...services import deposits as deposits_svc
from ...services import draft as draft_svc
from ...services import game as game_svc
from ...services import picks as picks_svc
from ...services import players as players_svc
from ...services import scoring
from .common import current_player, ensure_registered, esc, notify, reply

HELP_TEXT = (
    "*WC 2026 Sweepstake*\n\n"
    "1. /start — register\n"
    "2. /deposit — submit your entry (admin approves)\n"
    "3. /draft — see the draw order & whose turn it is\n"
    "4. /pick — draw your random teams when it's your turn\n\n"
    "Anytime:\n"
    "/mypicks — your teams & points\n"
    "/leaderboard — standings & pot\n"
    "/teams — team availability\n"
    "/fixtures — recent & upcoming results\n\n"
    "*Scoring:* win = 3, draw = 1, loss = 0 (all stages). "
    "Knockout shootout winner gets the win. Most points takes the whole pot."
)


@ensure_registered
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    with session_scope() as session:
        player = current_player(session, update)
        status = player.status if player else PlayerStatus.PENDING_DEPOSIT
        phase = game_svc.get_state(session).phase
        next_step = _next_step(session, player, status, phase, settings)
    await reply(
        update,
        f"👋 Welcome to the WC 2026 sweepstake, {esc(update.effective_user.first_name)}!\n\n"
        f"Entry: *{settings.entry_amount:g} {settings.currency}* — winner takes the whole pot.\n"
        f"You're drawn *{settings.teams_per_player}* random teams; "
        "win = 3, draw = 1, loss = 0 (all stages).\n\n"
        f"👉 {next_step}\n\n"
        "Use /help for all commands.",
        parse_mode=ParseMode.MARKDOWN,
    )


def _next_step(session, player, status, phase, settings) -> str:
    """A single, status-aware 'do this next' line so players never wonder what to do."""
    if status == PlayerStatus.REJECTED:
        return "Your deposit was rejected — talk to the admin, then /deposit again."
    if status == PlayerStatus.PENDING_DEPOSIT:
        return "Pay your entry, then send /deposit to register your payment."
    if status == PlayerStatus.DEPOSIT_SUBMITTED:
        return "Your deposit is in — waiting for an admin to approve it. Sit tight!"
    # ACTIVE or LOCKED
    if phase in (GamePhase.SETUP, GamePhase.REGISTRATION):
        return "You're approved ✅ — the draw hasn't started yet. Watch for your turn."
    if phase == GamePhase.PICKING:
        if draft_svc.current_player_id(session) == (player.id if player else None):
            return "It's *your turn* — run /pick to draw your teams!"
        otc = draft_svc.status_summary(session)["on_the_clock"]
        who = esc(otc.display_name) if otc else "someone"
        return f"Draw in progress — {who} is on the clock. See /draft."
    if phase == GamePhase.FINISHED:
        return "The game's over — check /leaderboard for the final result. 🏆"
    return "You're in! Track the race with /leaderboard and /mypicks."


@ensure_registered
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply(update, HELP_TEXT, parse_mode=ParseMode.MARKDOWN)


@ensure_registered
async def deposit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    note = " ".join(context.args) if context.args else None
    with session_scope() as session:
        player = current_player(session, update)
        try:
            deposits_svc.submit(session, player, note=note)
        except deposits_svc.DepositError as exc:
            await reply(update, f"⚠️ {exc}")
            return
    await reply(
        update,
        "✅ Deposit recorded. An admin will approve it shortly — you'll be able to "
        "draft once you're approved.",
    )


@ensure_registered
async def mypicks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from ...models import Outcome, ScoreEvent

    with session_scope() as session:
        player = current_player(session, update)
        picks = picks_svc.player_picks(session, player.id)
        if not picks:
            await reply(update, "You haven't been drawn any teams yet. Use /pick on your turn.")
            return

        # Per-team record from the ledger: points and W/D/L counts.
        record: dict[int, dict] = {}
        for ev in session.scalars(
            select(ScoreEvent).where(ScoreEvent.player_id == player.id)
        ):
            r = record.setdefault(ev.team_id, {"pts": 0, "W": 0, "D": 0, "L": 0})
            r["pts"] += ev.points
            r["W" if ev.outcome == Outcome.WIN else "D" if ev.outcome == Outcome.DRAW else "L"] += 1

        total = sum(r["pts"] for r in record.values())
        lines = [f"*Your teams* — total *{total}* pts"]
        for p in picks:
            t = p.team
            r = record.get(t.id)
            elim = " ❌" if t.eliminated else ""
            if r:
                detail = f"{r['pts']} pts ({r['W']}W {r['D']}D {r['L']}L)"
            else:
                detail = "no games yet"
            lines.append(f"  {t.flag_emoji or ''} {esc(t.name)}{elim} — {detail}".rstrip())
    await reply(update, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@ensure_registered
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from ...models import Pick

    settings = get_settings()
    with session_scope() as session:
        ranked = {s.player_id: s for s in scoring.leaderboard(session)}
        all_players = players_svc.active_players(session)
        names = {p.id: esc(p.display_name or p.username or str(p.telegram_id)) for p in all_players}

        # Each player's teams (with elimination state) for the sub-line.
        teams_by_player: dict[int, list] = {}
        for pk in session.scalars(select(Pick)):
            teams_by_player.setdefault(pk.player_id, []).append(pk.team)

        pot = deposits_svc.pot_total(session)
        winner_id = game_svc.get_state(session).winner_player_id

        rows = sorted(
            (
                (
                    (s.points if (s := ranked.get(p.id)) else 0),
                    (s.wins if s else 0),
                    (s.goal_diff if s else 0),
                    (s.goals_for if s else 0),
                    p.id,
                )
                for p in all_players
            ),
            reverse=True,
        )

        lines = ["🏆 *Leaderboard*"]
        for i, (pts, wins, gd, _gf, pid) in enumerate(rows, 1):
            crown = " 👑" if pid == winner_id else ""
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
            lines.append(f"{medal} {names.get(pid, pid)} — *{pts}* pts · {wins}W · GD {gd:+d}{crown}")
            squad = " ".join(
                f"{t.flag_emoji or ''}{esc(t.name)}{'❌' if t.eliminated else ''}"
                for t in teams_by_player.get(pid, [])
            )
            if squad:
                lines.append(f"     {squad}")
        if not rows:
            lines.append("_No players yet._")
        lines.append(f"\n💰 Pot: *{pot:g} {settings.currency}* (winner takes all)")
        if winner_id:
            lines.append(f"🎉 Winner: *{names.get(winner_id, winner_id)}*")
    await reply(update, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@ensure_registered
async def teams(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with session_scope() as session:
        from ...models import Pick, Player, Team

        owner = {
            pk.team_id: session.get(Player, pk.player_id)
            for pk in session.scalars(select(Pick))
        }
        all_teams = list(
            session.scalars(select(Team).order_by(Team.group_letter, Team.name))
        )
        lines = ["*Teams* (✅ free · 🔒 taken)"]
        current_group = None
        for t in all_teams:
            if t.group_letter != current_group:
                current_group = t.group_letter
                lines.append(f"\n*Group {current_group}*")
            o = owner.get(t.id)
            tag = f"🔒 {esc(o.display_name)}" if o else "✅"
            elim = " ❌" if t.eliminated else ""
            lines.append(f"  {t.flag_emoji or ''} {esc(t.name)} — {tag}{elim}".rstrip())
    await reply(update, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@ensure_registered
async def fixtures(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from ...models import Match, MatchStatus

    with session_scope() as session:
        matches = list(
            session.scalars(select(Match).order_by(Match.kickoff_utc.is_(None), Match.kickoff_utc))
        )
        if not matches:
            await reply(update, "No fixtures have been added yet.")
            return
        lines = ["*Fixtures*"]
        for m in matches:
            h, a = m.home_team.name, m.away_team.name
            h, a = esc(h), esc(a)
            if m.status == MatchStatus.FINISHED:
                score = f"{m.home_score}–{m.away_score}"
                if m.decided_by_pens and m.pen_winner_team_id:
                    pen = session.get(type(m.home_team), m.pen_winner_team_id)
                    score += f" (pens: {esc(pen.name)})"
                lines.append(f"  ✅ {h} {score} {a} [{m.stage.value}]")
            else:
                lines.append(f"  🕒 {h} vs {a} [{m.stage.value}]")
    await reply(update, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@ensure_registered
async def draft_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with session_scope() as session:
        info = draft_svc.status_summary(session)
        if info["status"].value == "NOT_STARTED":
            await reply(update, "The draft hasn't started yet. Hang tight!")
            return
        order_names = " → ".join(esc(p.display_name) for p in info["order"] if p)
        lines = [
            f"*Draft* — {info['status'].value.replace('_', ' ').title()}",
            f"Drawn: {info['picked_count']}/{info['total']} players",
            f"Order: {order_names}",
        ]
        otc = info["on_the_clock"]
        if otc:
            lines.append(f"⏳ On the clock: *{esc(otc.display_name)}* — run /pick to draw")
        lines.append(f"_seed: `{info['seed']}` (draw is verifiable)_")
    await reply(update, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@ensure_registered
async def pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Draw the player's random teams on their turn (teams are assigned, not chosen)."""
    from ...models import Player

    with session_scope() as session:
        player = current_player(session, update)
        if player.status not in (PlayerStatus.ACTIVE, PlayerStatus.LOCKED):
            await reply(update, "You need an approved deposit before you can draw. Try /deposit.")
            return
        state = game_svc.get_state(session)
        if state.phase != GamePhase.PICKING:
            await reply(update, "The draw isn't open right now.")
            return
        if draft_svc.current_player_id(session) != player.id:
            otc = draft_svc.status_summary(session)["on_the_clock"]
            who = esc(otc.display_name) if otc else "someone else"
            await reply(update, f"It's not your turn — {who} is on the clock. See /draft.")
            return
        try:
            teams = draft_svc.draw_for_player(session, player.id)
        except (draft_svc.DraftError, picks_svc.PickError) as exc:
            await reply(update, f"⚠️ {exc}")
            return
        team_str = ", ".join(f"{t.flag_emoji or ''} {esc(t.name)}".strip() for t in teams)
        next_id = draft_svc.current_player_id(session)
        next_name = None
        if next_id is not None:
            np = session.get(Player, next_id)
            next_name = (np.display_name, np.telegram_id) if np else None

    msg = f"🎲 You drew: {team_str}!\nGood luck. 🍀"
    await reply(update, msg, parse_mode=ParseMode.MARKDOWN)
    if next_name:
        await reply(update, f"Next up: *{esc(next_name[0])}* — run /pick.", parse_mode=ParseMode.MARKDOWN)
        await notify(context, next_name[1], "🎲 It's your turn — run /pick to draw your teams!")
    else:
        await reply(update, "🏁 Everyone has drawn — teams are locked. Let the games begin!")
