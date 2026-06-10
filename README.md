# WC 2026 Sweepstake Telegram Bot

A Telegram bot that runs a private World Cup 2026 sweepstake for a friend group:
registration, admin-approved deposits, a **randomized team draw** (unique teams),
result-driven scoring, a live leaderboard, and winner-takes-all resolution.

See [DESIGN.md](DESIGN.md) for the full design. This repo implements **Phase 0 + 1**
(fully-playable manual MVP) plus **Phase 2a** (auto results from football-data.org with
manual override). The admin web dashboard (Phase 2b) is still ahead.

## Rules

- Each player is **drawn 2 random teams** (teams are assigned, not chosen); every team is
  owned by exactly one player.
- Scoring (all stages): **win = 3, draw = 1, loss = 0**. A knockout decided on penalties
  awards the **shootout winner the 3-point win** (configurable).
- Fixed entry per player; the **winner takes the whole pot**.
- The draw order and every team drawn are seeded from a recorded value, so the entire
  outcome is reproducible and auditable.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env        # then fill in TELEGRAM_BOT_TOKEN and ADMIN_IDS
```

Get a bot token from [@BotFather](https://t.me/BotFather). Find your Telegram user id via
[@userinfobot](https://t.me/userinfobot) and put it in `ADMIN_IDS`.

## Run (local)

```bash
# 1. Seed the 48 teams (edit src/wcsweep/scripts/seed_teams.py to match the real draw)
.venv/bin/python -m wcsweep.scripts.seed_teams

# 2. Start the bot
.venv/bin/python -m wcsweep.bot.app
```

## Run (Docker, 24/7 with Postgres)

For a live tournament you want the bot — and the daily 06:00 poll — running continuously
on a server. The provided stack runs the bot against Postgres:

```bash
cp .env.example .env        # fill in TELEGRAM_BOT_TOKEN, ADMIN_IDS, FOOTBALL_DATA_API_KEY
# (DATABASE_URL in .env is ignored under compose — it points at the db service.)

docker compose up -d --build      # start bot + Postgres in the background
docker compose logs -f bot        # watch logs
docker compose down               # stop (data persists in the pgdata volume)
```

- The container seeds teams on startup (`SEED_ON_START=true`, idempotent) and creates the schema.
- Both services use `restart: unless-stopped`, so they survive reboots/crashes.
- Postgres data lives in the `pgdata` named volume. Set `POSTGRES_PASSWORD` in `.env` for a real password.
- `tzdata` is bundled in the image so `RESULTS_POLL_TZ` (e.g. `Africa/Addis_Ababa`) resolves.
- After first launch, run `/sync` once and `/mapteam` any unmatched team names.

## Typical flow

1. Admin: `/openreg` → players `/start` and `/deposit`.
2. Admin: `/pending` → approve each deposit (buttons), or `/approve <@user>`.
3. Admin: `/startdraft` → bot DMs each player on their turn; they `/pick` to be
   **drawn** their random teams. (Missed turns auto-draw after `DRAFT_TURN_SECONDS`;
   `/skipturn` forces it.)
4. Results come in **automatically** if `FOOTBALL_DATA_API_KEY` is set — the bot polls
   football-data.org once a day at `RESULTS_POLL_TIME` (`RESULTS_POLL_TZ`, default 06:00
   Africa/Addis_Ababa) and scores finished matches (announcing them to `GROUP_CHAT_ID` if
   set). `/sync` forces a pull any time. Admin can always
   override with `/setresult <match_id> <h> <a> [pen:<team>]` — manual results are never
   overwritten by the poller. Without an API key, add fixtures manually via `/addmatch`.
5. Everyone watches `/leaderboard`. When all relevant fixtures are done,
   admin runs `/endgame` to crown the winner.

### Team name matching

The poller matches API teams to your seeded teams by name (accent- and alias-aware) and
backfills the API id on first match. If a name doesn't match (the `/sync` report lists any),
bind it once with `/mapteam <team> <api_id>` and future syncs use the id directly.

## Tests

```bash
.venv/bin/pytest
```

Covers scoring (incl. penalties), unique team ownership, and the random draw order/turns.

## What's next

- ✅ ~~football-data.org results provider + scheduled polling~~ (done — Phase 2a)
- FastAPI admin dashboard reusing the same service layer (Phase 2b)
- Dockerfile + Postgres for deployment
