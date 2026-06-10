"""Results provider interface + normalized match shape.

Providers translate whatever an upstream API returns into a list of `ProviderMatch`,
so the ingest/reconciliation logic never sees provider-specific JSON. Swap providers
(football-data, API-Football, a fixture file for tests) without touching ingest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ...models import MatchStatus, Stage


@dataclass(frozen=True)
class ProviderTeam:
    api_id: int | None
    name: str


@dataclass(frozen=True)
class ProviderMatch:
    api_match_id: int
    stage: Stage
    home: ProviderTeam
    away: ProviderTeam
    kickoff_utc: datetime | None
    status: MatchStatus
    # Scores are after extra time (exclude any penalty shootout).
    home_score: int | None
    away_score: int | None
    decided_by_pens: bool
    pen_winner: str | None  # "HOME" | "AWAY" | None


class ResultsProvider(Protocol):
    def fetch_matches(self) -> list[ProviderMatch]:
        """Return all known fixtures/results for the competition."""
        ...
