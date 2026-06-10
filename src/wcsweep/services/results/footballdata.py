"""football-data.org v4 results provider.

Docs: https://docs.football-data.org/ — free tier needs an X-Auth-Token. The World Cup
competition code is "WC". We read /v4/competitions/WC/matches and normalize each match.

v4 score model: `score.fullTime` is the score after extra time, EXCLUDING any penalty
shootout; `score.penalties` holds the shootout; `score.duration` is one of
REGULAR / EXTRA_TIME / PENALTY_SHOOTOUT; `score.winner` accounts for penalties.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from ...models import MatchStatus, Stage
from .provider import ProviderMatch, ProviderTeam

BASE_URL = "https://api.football-data.org/v4"
COMPETITION = "WC"

_STAGE_MAP = {
    "GROUP_STAGE": Stage.GROUP,
    "LAST_32": Stage.R32,
    "ROUND_OF_32": Stage.R32,
    "LAST_16": Stage.R16,
    "ROUND_OF_16": Stage.R16,
    "QUARTER_FINALS": Stage.QF,
    "QUARTER_FINAL": Stage.QF,
    "SEMI_FINALS": Stage.SF,
    "SEMI_FINAL": Stage.SF,
    "THIRD_PLACE": Stage.THIRD,
    "3RD_PLACE": Stage.THIRD,
    "FINAL": Stage.FINAL,
}

_STATUS_MAP = {
    "SCHEDULED": MatchStatus.SCHEDULED,
    "TIMED": MatchStatus.SCHEDULED,
    "IN_PLAY": MatchStatus.LIVE,
    "PAUSED": MatchStatus.LIVE,
    "FINISHED": MatchStatus.FINISHED,
    # SUSPENDED / POSTPONED / CANCELLED / AWARDED -> treat as not-finished by default
}


def _parse_kickoff(value: str | None) -> datetime | None:
    if not value:
        return None
    # API returns e.g. "2026-06-11T19:00:00Z"
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        timezone.utc
    ).replace(tzinfo=None)


def map_match(raw: dict) -> ProviderMatch:
    """Translate one football-data match dict into a ProviderMatch (pure, testable)."""
    score = raw.get("score") or {}
    full = score.get("fullTime") or {}
    home_score = full.get("home")
    away_score = full.get("away")
    duration = (score.get("duration") or "REGULAR").upper()
    winner = score.get("winner")  # HOME_TEAM | AWAY_TEAM | DRAW | None

    decided_by_pens = (
        duration == "PENALTY_SHOOTOUT"
        and home_score is not None
        and home_score == away_score
    )
    pen_winner: str | None = None
    if decided_by_pens:
        if winner == "HOME_TEAM":
            pen_winner = "HOME"
        elif winner == "AWAY_TEAM":
            pen_winner = "AWAY"
        else:
            pens = score.get("penalties") or {}
            ph, pa = pens.get("home"), pens.get("away")
            if ph is not None and pa is not None and ph != pa:
                pen_winner = "HOME" if ph > pa else "AWAY"

    home = raw.get("homeTeam") or {}
    away = raw.get("awayTeam") or {}
    return ProviderMatch(
        api_match_id=raw["id"],
        stage=_STAGE_MAP.get((raw.get("stage") or "").upper(), Stage.GROUP),
        home=ProviderTeam(api_id=home.get("id"), name=home.get("name") or "?"),
        away=ProviderTeam(api_id=away.get("id"), name=away.get("name") or "?"),
        kickoff_utc=_parse_kickoff(raw.get("utcDate")),
        status=_STATUS_MAP.get((raw.get("status") or "").upper(), MatchStatus.SCHEDULED),
        home_score=home_score,
        away_score=away_score,
        decided_by_pens=decided_by_pens,
        pen_winner=pen_winner,
    )


class FootballDataProvider:
    """Live provider hitting football-data.org. Requires an API key."""

    def __init__(self, api_key: str, *, timeout: float = 15.0) -> None:
        if not api_key:
            raise ValueError("football-data API key is required")
        self._api_key = api_key
        self._timeout = timeout

    def fetch_matches(self) -> list[ProviderMatch]:
        resp = httpx.get(
            f"{BASE_URL}/competitions/{COMPETITION}/matches",
            headers={"X-Auth-Token": self._api_key},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return [map_match(m) for m in data.get("matches", [])]
