"""Results ingestion package."""

from __future__ import annotations

from ...config import Settings
from .footballdata import FootballDataProvider
from .provider import ProviderMatch, ProviderTeam, ResultsProvider
from .sync import SyncReport, ingest_matches

__all__ = [
    "FootballDataProvider",
    "ProviderMatch",
    "ProviderTeam",
    "ResultsProvider",
    "SyncReport",
    "build_provider",
    "ingest_matches",
]


def build_provider(settings: Settings) -> ResultsProvider | None:
    """Construct the configured provider, or None if no API key is set."""
    if settings.football_data_api_key:
        return FootballDataProvider(settings.football_data_api_key)
    return None
