"""Reconcile provider matches into our DB.

Rules (see DESIGN §9):
- Upsert matches by `api_match_id`.
- A match whose `source == MANUAL` is NEVER overwritten by the poller (admin override wins).
- Only matches where BOTH teams resolve to seeded teams are ingested (knockout fixtures
  often reference teams that aren't known until groups finish — those are skipped & reported).
- The score ledger is rebuilt ONCE at the end, not per match.
- Teams are matched by `api_team_id` first, then by normalized name (accent/alias-folded),
  backfilling `api_team_id` on a successful name match so future syncs are id-based.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Match, MatchStatus, ResultSource, Team, utcnow
from .. import game, scoring
from .provider import ProviderMatch, ProviderTeam

# Common name variants between providers and our seed. Keys/values are normalized.
_ALIASES = {
    "usa": "united states",
    "united states of america": "united states",
    "turkey": "turkiye",
    "ivory coast": "cote d ivoire",
    "korea republic": "south korea",
    "republic of korea": "south korea",
    "iran islamic republic of": "iran",
    "ir iran": "iran",
    "czech republic": "czechia",
    "cape verde": "cabo verde",
    "cape verde islands": "cabo verde",
    "dr congo": "democratic republic of congo",
    "congo dr": "democratic republic of congo",
}


def _normalize(name: str) -> str:
    """Lowercase, strip accents and punctuation, collapse spaces; then apply aliases."""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    cleaned = "".join(c if c.isalnum() or c.isspace() else " " for c in ascii_only)
    norm = " ".join(cleaned.lower().split())
    return _ALIASES.get(norm, norm)


@dataclass
class SyncReport:
    created: int = 0
    updated: int = 0
    skipped_manual: int = 0
    # Real teams we couldn't match by name, as (provider_name, api_id) so the admin can
    # /mapteam them directly. Excludes not-yet-drawn knockout placeholders.
    unmatched_teams: list[tuple[str, int]] = field(default_factory=list)
    # Matches skipped because a team isn't decided yet (knockout TBD, null api id).
    pending_fixtures: int = 0
    newly_finished: list[int] = field(default_factory=list)  # match ids

    @property
    def changed(self) -> bool:
        return bool(self.created or self.updated)


def _team_index(session: Session) -> tuple[dict[int, Team], dict[str, Team]]:
    by_api: dict[int, Team] = {}
    by_name: dict[str, Team] = {}
    for t in session.scalars(select(Team)):
        if t.api_team_id is not None:
            by_api[t.api_team_id] = t
        by_name[_normalize(t.name)] = t
    return by_api, by_name


def _resolve_team(
    pt: ProviderTeam, by_api: dict[int, Team], by_name: dict[str, Team]
) -> Team | None:
    if pt.api_id is not None and pt.api_id in by_api:
        return by_api[pt.api_id]
    team = by_name.get(_normalize(pt.name))
    if team is not None and team.api_team_id is None and pt.api_id is not None:
        team.api_team_id = pt.api_id  # backfill so next sync is id-based
        by_api[pt.api_id] = team
    return team


def ingest_matches(session: Session, matches: list[ProviderMatch]) -> SyncReport:
    """Reconcile provider matches into the DB and rebuild the ledger if anything changed."""
    by_api, by_name = _team_index(session)
    existing = {
        m.api_match_id: m
        for m in session.scalars(select(Match).where(Match.api_match_id.is_not(None)))
    }
    report = SyncReport()

    for pm in matches:
        home = _resolve_team(pm.home, by_api, by_name)
        away = _resolve_team(pm.away, by_api, by_name)
        if home is None or away is None:
            pending = False
            for pt, resolved in ((pm.home, home), (pm.away, away)):
                if resolved is not None:
                    continue
                if pt.api_id is not None:
                    report.unmatched_teams.append((pt.name, pt.api_id))
                else:
                    pending = True  # team not drawn yet (placeholder fixture)
            if pending:
                report.pending_fixtures += 1
            continue

        pen_winner_id = None
        if pm.decided_by_pens:
            pen_winner_id = home.id if pm.pen_winner == "HOME" else away.id

        match = existing.get(pm.api_match_id)
        if match is None:
            match = Match(api_match_id=pm.api_match_id, source=ResultSource.API)
            session.add(match)
            report.created += 1
        else:
            if match.source == ResultSource.MANUAL:
                report.skipped_manual += 1
                continue
            report.updated += 1

        was_finished = match.status == MatchStatus.FINISHED
        match.stage = pm.stage
        match.home_team_id = home.id
        match.away_team_id = away.id
        match.kickoff_utc = pm.kickoff_utc
        match.status = pm.status
        match.home_score = pm.home_score
        match.away_score = pm.away_score
        match.decided_by_pens = pm.decided_by_pens
        match.pen_winner_team_id = pen_winner_id
        match.source = ResultSource.API
        match.last_synced_at = utcnow()
        session.flush()

        if not was_finished and match.status == MatchStatus.FINISHED:
            report.newly_finished.append(match.id)

    if report.changed:
        scoring.rebuild_ledger(session)
        game.mark_eliminations(session)
    # de-dup (a team appears in several fixtures) and sort by name for a tidy report
    report.unmatched_teams = sorted(set(report.unmatched_teams), key=lambda x: x[0])
    return report
