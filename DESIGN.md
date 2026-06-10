# WC 2026 Sweepstake Telegram Bot — Design Doc

## 1. Overview

A Telegram bot that runs a private World Cup 2026 sweepstake for a group of friends.
The bot is the single source of truth: it registers players, tracks deposits (admin-approved),
lets each player draft **2 unique teams**, ingests match results, scores them, maintains a live
leaderboard, and declares a winner when the tournament ends (or when all of a player's teams are
eliminated and no contest remains).

**Stack:** Python 3.11+ · `python-telegram-bot` v21 (async) · SQLAlchemy + SQLite (dev) / Postgres (prod) · FastAPI admin dashboard · APScheduler for polling · football-data API for results.

---

## 2. Goals / Non-Goals

**Goals**
- Frictionless player onboarding inside Telegram (`/start` → register → pick teams).
- Admin-controlled money flow: a player is only "active" once their deposit is approved.
- **Unique team ownership** — each of the 48 WC teams can belong to at most one player.
- Auto-ingest results from an API, with an admin override to fix/confirm any match.
- Live leaderboard with transparent, auditable scoring.
- Deterministic winner resolution, including tie-breakers.

**Non-Goals (v1)**
- Real payment processing (deposits are tracked, not collected — money moves out-of-band).
- Multiple concurrent tournaments / leagues (single global sweepstake instance).
- In-bot disputes/chat. Disputes handled by admin override.

---

## 3. Roles

| Role | Capabilities |
|------|-------------|
| **Player** | Register, pick 2 teams (while open), view own picks, view leaderboard, view fixtures/results. |
| **Admin** | All player actions + approve/reject deposits, open/close registration & picking, override match results, force-recompute scores, declare/lock the game, manage players. |

Admins are defined by a `ADMIN_TELEGRAM_IDS` env list (and an `is_admin` flag in DB).

---

## 4. Player Flow

```
/start
  └─ Register (capture telegram_id, username, display name)
        └─ Status: PENDING_DEPOSIT
              └─ Player sends money out-of-band → /deposit (marks intent + optional note/screenshot)
                    └─ Admin approves  →  Status: ACTIVE
                          └─ Admin starts the draw → each player runs /pick on their turn
                                └─ Bot draws their random teams → picks locked when all have drawn
                                      └─ Matches play → scores update → /leaderboard
                                            └─ Game ends → winner announced
```

### Status machine (Player)
`PENDING_DEPOSIT → DEPOSIT_SUBMITTED → ACTIVE → (PICKED) → LOCKED`
Reject path: `DEPOSIT_SUBMITTED → PENDING_DEPOSIT` (with admin reason).

---

## 5. Scoring Rules

Per team, per match the team plays:

| Outcome | Points |
|---------|--------|
| Win | 3 |
| Draw | 1 |
| Loss | 0 |

- Applies to **all stages** — group and knockout alike.
- **Knockout draws decided on penalties:** the **penalty shootout winner earns the 3-point win**;
  the loser gets 0. (Group decision — `KO_PENALTY_AS_DRAW=false`. Flip to `true` to instead count
  a penalty result as draw=1 for both.) The `pen_winner_team_id` on the match drives the outcome.
- A player's score = sum of points across **both** their teams, across **every** match those teams play.
- Teams that advance further play more matches → more scoring opportunities (this is the core skill/luck of the draft).

### Winner & tie-breakers (in order)
1. Highest total points.
2. Most wins (across both teams).
3. Higher goal difference of owned teams (goals scored − conceded in matches played).
4. Most goals scored by owned teams.
5. Coin flip (admin `/breaktie` — recorded with a seed for auditability).

### Prize
**Fixed entry amount** per player (config `ENTRY_AMOUNT`), **winner-takes-all** — the whole pot
goes to the winning player. Pot = `ENTRY_AMOUNT × (approved players)`, shown on the leaderboard.
Payout itself happens out-of-band; the bot just displays the pot and winner.

### Game end condition
The game is **complete** when **either**:
- The WC 2026 final has been played (all fixtures `FINISHED`), **or**
- Every still-relevant team is eliminated such that the leaderboard order can no longer change
  (i.e. no remaining fixture involves any owned team that could alter standings).

Admin can also `/endgame` manually to force resolution.

---

## 5b. The Draw (random team assignment)

Teams are **not chosen** — they're **drawn at random**. There's no skill in selection, so
fairness comes purely from randomness, and the recorded seed makes the whole draw verifiable.

1. **Order:** when all deposits are in and the admin runs `/startdraft`, the bot shuffles the
   approved players into a random order using a recorded `seed` (stored on the `draft` row so the
   order is reproducible/auditable — anyone can verify it wasn't rigged). The bot also checks
   there are enough teams in the pool (`players × TEAMS_PER_PLAYER`).
2. **One turn each:** going through that order, each player runs `/pick` on their turn and the bot
   **draws their full allocation** (`TEAMS_PER_PLAYER`, default 2) of random teams from the
   available pool at once. No snake/rounds — a single pass, since random draws give no pick
   advantage to offset. Taken teams are excluded (enforced by `UNIQUE(team_id)`).
3. **Determinism:** each draw is seeded from `"{seed}:draw:{index}"`, so given the seed the entire
   outcome (order *and* every team drawn) is reproducible and can be re-derived for disputes.
4. **Clock:** each turn has a `turn_deadline_utc` (config `DRAFT_TURN_SECONDS`, e.g. 12h, async-friendly).
   If a player misses the deadline (or admin runs `/skipturn`), the bot **auto-draws** for them and advances.
5. **Completion:** once every player has drawn, `draft.status=COMPLETE`, game phase → `RUNNING`, picks locked.

### Future variant — small group, restricted pool
For a 3-friend game drawing from, say, the **top-9 teams by world ranking** (3 each), only two
things change: the eligible **pool** is narrowed and `TEAMS_PER_PLAYER` is raised. The draw logic
is identical. This lives behind `draft._eligible_teams()` (currently returns all seeded teams) —
the single seam to extend. Not built yet; flagged so the model stays compatible.

## 6. Data Model

```
players
  id (pk)
  telegram_id (unique, indexed)
  username
  display_name
  status            ENUM(PENDING_DEPOSIT, DEPOSIT_SUBMITTED, ACTIVE, LOCKED, REJECTED)
  is_admin          bool
  created_at, updated_at

deposits
  id (pk)
  player_id (fk)
  amount            decimal (nullable until confirmed)
  currency          default config
  note              text          # player message / reference
  proof_file_id     text          # telegram file_id of screenshot (optional)
  status            ENUM(SUBMITTED, APPROVED, REJECTED)
  reviewed_by       fk players    # admin
  review_note       text
  created_at, reviewed_at

teams                              # seeded from WC 2026 team list (48)
  id (pk)
  api_team_id       int unique     # id in the results API
  name
  group_letter      char
  flag_emoji
  eliminated        bool default false

picks
  id (pk)
  player_id (fk)
  team_id (fk, UNIQUE)             # <-- enforces global unique ownership
  pick_order        smallint (1..2)   # which of the player's 2 slots
  round_no          smallint (1..2)   # slot index within the player's allocation
  created_at
  UNIQUE(team_id)                  # one team -> one player
  CHECK count per player <= 2      # enforced in app logic + partial constraint

draft                              # singleton row; the randomized draw
  id (pk = 1)
  seed              text           # RNG seed: shuffles order AND drives each draw (audit)
  order_player_ids  json           # shuffled player order, e.g. [3,1,4,2]
  current_index     int            # which player in the order is on the clock
  current_round     smallint       # retained for schema stability (single pass = 1)
  turn_deadline_utc datetime nullable   # when current pick auto-resolves
  status            ENUM(NOT_STARTED, RUNNING, COMPLETE)

matches                            # fixtures
  id (pk)
  api_match_id      int unique
  stage             ENUM(GROUP, R32, R16, QF, SF, THIRD, FINAL)
  home_team_id (fk)
  away_team_id (fk)
  kickoff_utc       datetime
  status            ENUM(SCHEDULED, LIVE, FINISHED)
  home_score        int nullable   # after extra time
  away_score        int nullable
  decided_by_pens   bool default false
  pen_winner_team_id fk nullable
  source            ENUM(API, MANUAL)   # provenance for override audit
  last_synced_at    datetime

score_events                       # immutable ledger, one row per (team, match)
  id (pk)
  player_id (fk)
  team_id (fk)
  match_id (fk)
  outcome           ENUM(WIN, DRAW, LOSS)
  points            int
  created_at
  UNIQUE(team_id, match_id)        # idempotent recompute

game_state                         # singleton row
  id (pk = 1)
  phase             ENUM(SETUP, REGISTRATION, PICKING, RUNNING, FINISHED)
  registration_open bool
  picking_open      bool
  picks_locked_at   datetime
  winner_player_id  fk nullable
  tiebreak_seed     text nullable
```

The **leaderboard** is a query (sum of `score_events.points` grouped by player), not a stored table —
always derivable from the immutable ledger, so recompute is safe and auditable.

---

## 7. Bot Commands

### Player
| Command | Action |
|---------|--------|
| `/start` | Register / show status & menu |
| `/deposit` | Submit deposit intent (optionally attach screenshot) |
| `/pick` | When it's your turn: bot **draws** your random teams (no choosing) |
| `/draft` | Show draft status: order, whose turn, who's on the clock, your upcoming pick |
| `/mypicks` | Show your 2 teams + their points |
| `/leaderboard` | Live standings |
| `/fixtures` | Upcoming + recent matches involving owned teams |
| `/teams` | List teams with availability (taken/free) |
| `/help` | Command list, rules summary |

### Admin
| Command | Action |
|---------|--------|
| `/admin` | Admin menu |
| `/pending` | List deposits awaiting review (inline approve/reject) |
| `/approve <player>` / `/reject <player> <reason>` | Deposit decision |
| `/openreg` `/closereg` | Toggle registration |
| `/startdraft` | Shuffle approved players (recorded seed) & open the random draw |
| `/skipturn` | Force-advance / auto-pick for a stalled player |
| `/lockpicks` | End draft early & lock (e.g. at kickoff) |
| `/setresult <match> <h> <a> [pens:<team>]` | Manual result / override |
| `/sync` | Force pull results from API now |
| `/recompute` | Rebuild score ledger from matches |
| `/endgame` | Resolve winner + lock |
| `/breaktie` | Run recorded tie-break |
| `/broadcast <msg>` | Message all active players |

---

## 8. Admin Dashboard (Phase 2)

Small FastAPI + HTMX/Jinja app (single-admin auth via token or Telegram login widget):

- **Deposits** table: pending/approved/rejected, approve/reject buttons, amount entry.
- **Players** table: status, picks, total points.
- **Matches** table: inline-editable scores, "override" toggle, sync button.
- **Leaderboard** read-only live view (good for sharing a screen during matchdays).
- **Audit log**: score_events + result overrides.

Phase 1 ships bot-only admin commands; the dashboard reuses the same service layer.

---

## 9. Results Ingestion

- **Source:** [football-data.org](https://www.football-data.org/) WC 2026 competition (free tier; fallback API-Football). Abstract behind a `ResultsProvider` interface so the source is swappable.
- **Polling:** a daily JobQueue job (default 06:00 `Africa/Addis_Ababa`, config `RESULTS_POLL_TIME`/`RESULTS_POLL_TZ`) pulls the full fixture list; `/sync` forces a pull any time. Once-daily is enough since group games settle overnight.
- **Reconciliation:** upsert matches by `api_match_id`. When a match flips to `FINISHED`, write/refresh `score_events` (idempotent via `UNIQUE(team_id, match_id)`).
- **Override:** `/setresult` or dashboard sets `source=MANUAL`; the poller **never overwrites** a `MANUAL` match unless admin re-enables sync for it.
- **Elimination tracking:** when a team can no longer play another match, mark `eliminated=true` (used for the game-end check and `/teams` display).

---

## 10. Architecture

```
telegram <──> bot (python-telegram-bot, async handlers)
                     │  calls
                     ▼
              service layer  ──────────────┐
              (players, picks, scoring,     │  shared
               deposits, game_state)        │
                     │                       ▼
                     ▼                 FastAPI dashboard (phase 2)
              SQLAlchemy models
                     │
                     ▼
              SQLite / Postgres

  APScheduler ──> ResultsProvider (football-data) ──> service.ingest_results()
```

- **Service layer is UI-agnostic** — both the bot and the dashboard call the same functions. No business logic in handlers.
- Scoring is pure: `compute_outcome(match, team) -> (outcome, points)`; the ledger makes recompute deterministic.

---

## 11. Game Lifecycle (game_state.phase)

`SETUP` → admin seeds teams & fixtures
`REGISTRATION` → players join + deposit
`PICKING` → each approved player runs /pick and is drawn 2 random unique teams (§5b)
`RUNNING` → picks locked, results flow in, leaderboard live
`FINISHED` → winner declared, board frozen

Transitions are admin-driven commands; some auto-suggest (e.g. bot nudges admin to lock picks at first kickoff).

---

## 12. Edge Cases & Decisions

- **Not enough teams:** 48 teams ÷ 2 = max **24 players**. Bot blocks the 25th pick attempt and warns admin as availability runs low.
- **Player misses their draft turn:** bot auto-picks a random available team after `DRAFT_TURN_SECONDS` and advances (so the draft never stalls). Admin `/skipturn` forces it manually.
- **Deposit rejected after picking:** picks released back to the pool, player → PENDING_DEPOSIT. (Best handled before `/startdraft`; if mid-draft, admin re-runs affected turns.)
- **Draft fairness audit:** the shuffle `seed` is stored, so the order can be re-derived and verified by anyone — no hidden RNG.
- **API gives wrong/late score:** admin override wins; `/recompute` rebuilds ledger.
- **Penalty shootouts:** see §5 (`KO_PENALTY_AS_DRAW`).
- **Double-pick race:** `UNIQUE(team_id)` + transaction ensures two players can't grab the same team simultaneously.
- **WC 2026 third-place qualifiers:** group standings logic lives in the API; bot only consumes match results, so it's format-agnostic.

### Resolved decisions
1. **Entry amount: fixed** (`ENTRY_AMOUNT`). Pot = amount × approved players.
2. **Prize: winner-takes-all** (display only; payout out-of-band).
3. **Penalty rule: shootout winner = 3-point win** (`KO_PENALTY_AS_DRAW=false`).
4. **Team assignment: random draw** (teams are drawn, not chosen), one turn per player, recorded seed for reproducibility/auditability (§5b). Future: restricted pool (top-N) for small groups.

---

## 13. Project Structure

```
wc-sweepstake/
├── DESIGN.md
├── README.md
├── pyproject.toml
├── .env.example
├── alembic/                 # migrations
├── src/wcsweep/
│   ├── __init__.py
│   ├── config.py            # env, settings
│   ├── db.py                # engine, session
│   ├── models.py            # SQLAlchemy models
│   ├── bot/
│   │   ├── app.py           # bot bootstrap, handler registration, job wiring
│   │   ├── jobs.py          # results polling job + shared /sync routine
│   │   ├── handlers/        # common, player, admin
│   │   └── keyboards.py     # inline keyboards (deposit review)
│   ├── services/
│   │   ├── players.py
│   │   ├── deposits.py
│   │   ├── picks.py
│   │   ├── draft.py         # randomized draw
│   │   ├── scoring.py
│   │   ├── game.py
│   │   └── results/
│   │       ├── __init__.py  # build_provider() factory
│   │       ├── provider.py  # ResultsProvider interface + ProviderMatch
│   │       ├── footballdata.py
│   │       └── sync.py      # ingest/reconciliation (override-safe)
│   ├── scripts/seed_teams.py
│   └── dashboard/           # FastAPI app (phase 2b — TODO)
└── tests/
    ├── test_scoring.py · test_picks_unique.py · test_draft.py
    └── test_results_provider.py · test_results_sync.py
```
Uses PTB's built-in JobQueue for polling (not a standalone APScheduler module).

---

## 14. Implementation Phases

**Phase 0 — Scaffold**
Repo, `pyproject.toml`, config, DB models, migrations, `.env.example`, bot bootstrap that responds to `/start`.

**Phase 1 — Core loop (MVP)** ✅ done
Registration · deposit submit + admin approve · seed teams · random `/pick` draw · `/leaderboard` · manual `/setresult` · scoring ledger + recompute.

**Phase 2a — Auto results** ✅ done
football-data provider + JobQueue polling · API-with-override reconciliation (manual results never clobbered) · accent/alias team-name matching + `/mapteam` · `/sync` · group FT announcements · elimination tracking.

**Phase 2b — Dashboard** (TODO)
FastAPI admin dashboard reusing the service layer · shareable live leaderboard.

**Phase 3 — Polish**
Nicer leaderboard formatting · deploy (Docker + Postgres) · auto game-end detection.

---

## 15. Config (.env)

```
TELEGRAM_BOT_TOKEN=
ADMIN_IDS=123,456            # comma-separated Telegram user ids
GROUP_CHAT_ID=              # optional: where FT announcements are posted
DATABASE_URL=sqlite:///wcsweep.db
FOOTBALL_DATA_API_KEY=
RESULTS_POLL_TIME=06:00            # daily poll time
RESULTS_POLL_TZ=Africa/Addis_Ababa # EAT (UTC+3)
ENTRY_AMOUNT=10              # fixed entry per player; pot = amount × approved players
CURRENCY=USD
KO_PENALTY_AS_DRAW=false     # penalty shootout winner gets the 3-point win
DRAFT_TURN_SECONDS=43200     # 12h per draft turn before auto-pick
TIMEZONE=UTC
```
```
```
