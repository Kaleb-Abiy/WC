"""Application configuration, loaded from environment / .env."""

from __future__ import annotations

from functools import cached_property, lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Telegram
    telegram_bot_token: str = ""
    # Comma-separated in the env (e.g. "123,456"); parse via admin_telegram_ids below.
    admin_ids: str = ""
    group_chat_id: int | None = None

    @cached_property
    def admin_telegram_ids(self) -> list[int]:
        return [int(x) for x in self.admin_ids.replace(" ", "").split(",") if x]

    # Database
    database_url: str = "sqlite:///wcsweep.db"

    # Results provider (Phase 2)
    football_data_api_key: str = ""
    # The poller runs once a day at this local time / timezone.
    results_poll_time: str = "06:00"  # HH:MM
    results_poll_tz: str = "Africa/Addis_Ababa"  # EAT (UTC+3, no DST)

    # Game rules
    entry_amount: float = 10.0
    currency: str = "USD"
    teams_per_player: int = 2
    ko_penalty_as_draw: bool = False
    draft_turn_seconds: int = 43_200  # 12h
    timezone: str = "UTC"


@lru_cache
def get_settings() -> Settings:
    return Settings()
