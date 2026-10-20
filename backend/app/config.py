"""Application settings, loaded from environment / .env only.

Secrets are SecretStr so they never appear in repr() or logs.
"""
from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExecutionMode(str, Enum):
    """BACKTEST is the default. There is deliberately no LIVE member:
    live trading does not exist in this release."""

    BACKTEST = "BACKTEST"
    RESEARCH = "RESEARCH"
    PAPER = "PAPER"
    DEMO = "DEMO"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="TRADE1_", extra="ignore")

    env: str = "development"
    database_url: str = "postgresql+psycopg://trade1:trade1@localhost:5432/trade1"
    api_token: SecretStr | None = None  # required for every route except /api/health
    log_level: str = "INFO"

    # Requested mode. DEMO additionally needs the demo gate (Phase E) to pass;
    # an invalid value fails startup rather than falling back to something else.
    execution_mode: ExecutionMode = ExecutionMode.BACKTEST

    # MT5 terminal. Credentials are optional: if empty, the account already
    # logged in to the terminal is used.
    mt5_path: str | None = None
    mt5_login: int | None = None
    mt5_password: SecretStr | None = None
    mt5_server: str | None = None
    mt5_timeout_ms: int = Field(default=60_000, ge=1_000)
    # Offset of the broker's trade-server clock from UTC. MT5 returns bar and
    # tick times in server time. Verify it with `python -m app.cli mt5-check`.
    mt5_server_utc_offset_hours: float = 0.0

    # Optional economic-calendar integration (disabled unless a URL is set).
    news_feed_url: str | None = None
    news_min_refresh_minutes: int = Field(default=60, ge=15)
    news_blackout_before_min: int = Field(default=30, ge=0, le=720)
    news_blackout_after_min: int = Field(default=30, ge=0, le=720)
    news_impacts: tuple[str, ...] = ("high",)
    news_categories: tuple[str, ...] = ("forex", "metals", "energy", "indices")

    # Demo execution limits (engineering defaults for testing, not proven optimal values)
    demo_magic: int = 770011                      # tags Trade1's orders; other trades are never touched
    demo_risk_per_trade_pct: float = Field(default=0.25, gt=0, le=2)
    demo_max_daily_loss_pct: float = Field(default=1.0, gt=0, le=10)
    demo_max_drawdown_pct: float = Field(default=10.0, gt=0, le=50)
    demo_max_open_positions: int = Field(default=3, ge=1, le=20)
    demo_max_spread_multiple: float = Field(default=3.0, ge=1, le=20)  # vs spread seen at discovery
    demo_max_slippage_points: int = Field(default=30, ge=0, le=10_000)
    demo_max_tick_age_seconds: int = Field(default=60, ge=5, le=3600)

    # Optional Telegram alerts (both must be set)
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    @field_validator("mt5_login", mode="before")
    @classmethod
    def _empty_login(cls, v):
        return None if v in ("", None) else v


@lru_cache
def get_settings() -> Settings:
    return Settings()
