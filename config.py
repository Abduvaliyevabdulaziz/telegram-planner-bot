"""
Configuration and Environment Settings for the Telegram Bot.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Load environment variables from .env file if python-dotenv is installed
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent

# Timezone constant - Asia/Tashkent (UTC+5)
TIMEZONE = ZoneInfo("Asia/Tashkent")


@dataclass(frozen=True)
class Settings:
    # Bot & Admin Security
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()
    ADMIN_USER_ID: int = int(os.getenv("ADMIN_USER_ID", "0").strip() or "0")

    # Financial thresholds
    DAILY_BUDGET_LIMIT: int = int(os.getenv("DAILY_BUDGET_LIMIT", "70000").strip() or "70000")

    # Timezone
    TIMEZONE_NAME: str = "Asia/Tashkent"

    # Database
    DB_PATH: Path = BASE_DIR / os.getenv("DB_FILE_NAME", "planner_bot.db").strip()
    DATABASE_URL: str = os.getenv("DATABASE_URL", "").strip()

    # Scheduler fixed hours (Tashkent local time)
    EVENING_PLAN_HOUR: int = int(os.getenv("EVENING_PLAN_HOUR", "21").strip() or "21")
    EVENING_PLAN_MINUTE: int = int(os.getenv("EVENING_PLAN_MINUTE", "0").strip() or "0")
    MORNING_BRIEF_HOUR: int = int(os.getenv("MORNING_BRIEF_HOUR", "9").strip() or "9")
    MORNING_BRIEF_MINUTE: int = int(os.getenv("MORNING_BRIEF_MINUTE", "0").strip() or "0")

    def validate(self) -> None:
        """Validate required configuration values."""
        if not self.BOT_TOKEN:
            raise ValueError(
                "BOT_TOKEN is missing! Please specify BOT_TOKEN in your .env file or environment variables."
            )
        if self.ADMIN_USER_ID <= 0:
            raise ValueError(
                "ADMIN_USER_ID is missing or invalid! Please specify your numeric Telegram User ID in .env."
            )


# Global settings instance
settings = Settings()


# --- Timezone & Date Helper Functions ---

def get_now() -> datetime:
    """Return current datetime in Asia/Tashkent timezone."""
    return datetime.now(TIMEZONE)


def get_today_str() -> str:
    """Return today's date formatted as YYYY-MM-DD in Asia/Tashkent."""
    return get_now().strftime("%Y-%m-%d")


def get_tomorrow_str() -> str:
    """Return tomorrow's date formatted as YYYY-MM-DD in Asia/Tashkent."""
    return (get_now() + timedelta(days=1)).strftime("%Y-%m-%d")


def get_yesterday_str() -> str:
    """Return yesterday's date formatted as YYYY-MM-DD in Asia/Tashkent."""
    return (get_now() - timedelta(days=1)).strftime("%Y-%m-%d")


def format_dt(dt: datetime) -> str:
    """Format datetime as YYYY-MM-DD HH:MM:SS."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def format_currency(amount: int | float) -> str:
    """Format integer/float amount with thousand separators and UZS suffix (e.g. 45,000 UZS)."""
    return f"{int(round(amount)):,} UZS"
