"""
Comprehensive test suite for the Telegram Planner Bot.
Tests parsing, timezone calculations, database operations, reports, and security middleware.
"""
import sys
from unittest.mock import MagicMock

# Gracefully mock aiogram and apscheduler if testing in an environment without installed packages
class MockState:
    def __init__(self, *args, **kwargs):
        pass

class MockStatesGroup:
    pass

aiogram_fsm_state = MagicMock()
aiogram_fsm_state.State = MockState
aiogram_fsm_state.StatesGroup = MockStatesGroup
sys.modules["aiogram.fsm.state"] = aiogram_fsm_state

class MockBaseMiddleware:
    def __init__(self, *args, **kwargs):
        pass

class MockTelegramObject:
    pass

class MockMessage(MockTelegramObject):
    pass

class MockCallbackQuery(MockTelegramObject):
    pass

aiogram_mock = MagicMock()
aiogram_mock.BaseMiddleware = MockBaseMiddleware
sys.modules["aiogram"] = aiogram_mock

aiogram_types_mock = MagicMock()
aiogram_types_mock.TelegramObject = MockTelegramObject
aiogram_types_mock.Message = MockMessage
aiogram_types_mock.CallbackQuery = MockCallbackQuery
sys.modules["aiogram.types"] = aiogram_types_mock

for mod in [
    "aiogram.enums",
    "aiogram.filters",
    "aiogram.fsm",
    "aiogram.fsm.context",
    "aiogram.fsm.storage",
    "aiogram.fsm.storage.memory",
    "aiogram.utils",
    "aiogram.utils.keyboard",
    "aiosqlite",
    "apscheduler",
    "apscheduler.schedulers",
    "apscheduler.schedulers.asyncio",
    "apscheduler.triggers",
    "apscheduler.triggers.cron",
    "apscheduler.triggers.date",
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import sqlite3

from config import (
    TIMEZONE,
    format_currency,
    get_today_str,
    get_tomorrow_str,
    get_yesterday_str,
)
from handlers.expenses import parse_expense_text, format_report_message
from handlers.planner import parse_relative_time, parse_remind_command


class TestParsers(unittest.TestCase):
    def test_parse_expense_text(self):
        # Basic
        self.assertEqual(parse_expense_text("45000 Fast food"), (45000, "Fast food"))
        self.assertEqual(parse_expense_text("25000 coffee"), (25000, "coffee"))

        # Shorthand 'k'
        self.assertEqual(parse_expense_text("12k taxi"), (12000, "taxi"))
        self.assertEqual(parse_expense_text("12.5k lunch"), (12500, "lunch"))
        self.assertEqual(parse_expense_text("45K Shawarma"), (45000, "Shawarma"))

        # Cyrillic 'к'
        self.assertEqual(parse_expense_text("45к taxi"), (45000, "taxi"))
        self.assertEqual(parse_expense_text("12.5к lunch"), (12500, "lunch"))

        # Space separated numbers
        self.assertEqual(parse_expense_text("50 000 groceries"), (50000, "groceries"))

        # Default category when no note is provided
        self.assertEqual(parse_expense_text("70k"), (70000, "General"))
        self.assertEqual(parse_expense_text("1000"), (1000, "General"))

        # Invalid cases
        self.assertIsNone(parse_expense_text("0 bad"))
        self.assertIsNone(parse_expense_text("-5000 bad"))
        self.assertIsNone(parse_expense_text("hello 12k"))
        self.assertIsNone(parse_expense_text("/remind 18:30"))
        self.assertIsNone(parse_expense_text("/spent 2026-11-14"))
        self.assertIsNone(parse_expense_text(""))

    def test_parse_relative_time(self):
        # Minutes
        delta, task = parse_relative_time("45m Check oven")
        self.assertEqual(delta, timedelta(minutes=45))
        self.assertEqual(task, "Check oven")

        # Hours
        delta, task = parse_relative_time("2h Gym workout")
        self.assertEqual(delta, timedelta(hours=2))
        self.assertEqual(task, "Gym workout")

        # Compound hours and minutes
        delta, task = parse_relative_time("1h30m Team meeting")
        self.assertEqual(delta, timedelta(hours=1, minutes=30))
        self.assertEqual(task, "Team meeting")

        # Days
        delta, task = parse_relative_time("1d Call mom")
        self.assertEqual(delta, timedelta(days=1))
        self.assertEqual(task, "Call mom")

        # Invalid
        self.assertIsNone(parse_relative_time("invalid text"))
        self.assertIsNone(parse_relative_time("0m nothing"))

    def test_parse_remind_command(self):
        # Simulated 'now': 2026-11-14 21:56 Tashkent
        simulated_now = datetime(2026, 11, 14, 21, 56, tzinfo=TIMEZONE)

        # Time earlier today -> should automatically schedule for tomorrow
        target_dt, task = parse_remind_command("18:30 Call client", simulated_now)
        self.assertEqual(task, "Call client")
        self.assertEqual(target_dt, datetime(2026, 11, 15, 18, 30, tzinfo=TIMEZONE))

        # Time later tonight -> schedules for tonight
        target_dt, task = parse_remind_command("23:00 Go to sleep", simulated_now)
        self.assertEqual(task, "Go to sleep")
        self.assertEqual(target_dt, datetime(2026, 11, 14, 23, 0, tzinfo=TIMEZONE))

        # Explicit date & time
        target_dt, task = parse_remind_command(
            "2026-11-20 14:00 Submit project", simulated_now
        )
        self.assertEqual(task, "Submit project")
        self.assertEqual(target_dt, datetime(2026, 11, 20, 14, 0, tzinfo=TIMEZONE))

        # Invalid
        self.assertIsNone(parse_remind_command("invalid command", simulated_now))


class TestCalculationsAndReports(unittest.TestCase):
    def test_format_currency(self):
        self.assertEqual(format_currency(45000), "45,000 UZS")
        self.assertEqual(format_currency(70000), "70,000 UZS")
        self.assertEqual(format_currency(1234567), "1,234,567 UZS")

    def test_format_report_message(self):
        stats = {
            "total": 75000,
            "count": 2,
            "categories": [
                {"category": "Fast food", "amount": 45000, "percentage": 60.0},
                {"category": "coffee", "amount": 30000, "percentage": 40.0},
            ],
            "recent_transactions": [
                {"created_at": "2026-11-14 18:30:00", "amount": 45000, "category": "Fast food"},
                {"created_at": "2026-11-14 19:00:00", "amount": 30000, "category": "coffee"},
            ],
        }
        report = format_report_message("Today's Report", stats)
        self.assertIn("75,000 UZS", report)
        self.assertIn("Fast food", report)
        self.assertIn("60.0%", report)
        self.assertIn("coffee", report)
        self.assertIn("40.0%", report)


class TestDatabaseSchemaAndLogic(unittest.TestCase):
    def setUp(self):
        # Use in-memory SQLite to test all SQL queries synchronously
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            self.conn.execute(
                """
                CREATE TABLE tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    task_text TEXT NOT NULL,
                    target_date TEXT NOT NULL,
                    remind_at TEXT,
                    is_completed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                """
            )

    def tearDown(self):
        self.conn.close()

    def test_expense_logging_and_budget_alert_logic(self):
        user_id = 999
        today = "2026-11-14"

        # Log expense 1: 45,000 UZS
        self.conn.execute(
            "INSERT INTO expenses (user_id, amount, category, created_at) VALUES (?, ?, ?, ?)",
            (user_id, 45000, "Fast food", f"{today} 12:00:00"),
        )
        cursor = self.conn.execute(
            "SELECT SUM(amount) FROM expenses WHERE user_id = ? AND created_at LIKE ?",
            (user_id, f"{today}%"),
        )
        total1 = cursor.fetchone()[0]
        self.assertEqual(total1, 45000)
        self.assertLessEqual(total1, 70000)  # Under budget

        # Log expense 2: 30,000 UZS -> Total becomes 75,000 UZS (Exceeds 70,000 UZS!)
        self.conn.execute(
            "INSERT INTO expenses (user_id, amount, category, created_at) VALUES (?, ?, ?, ?)",
            (user_id, 30000, "Taxi", f"{today} 15:00:00"),
        )
        cursor = self.conn.execute(
            "SELECT SUM(amount) FROM expenses WHERE user_id = ? AND created_at LIKE ?",
            (user_id, f"{today}%"),
        )
        total2 = cursor.fetchone()[0]
        self.assertEqual(total2, 75000)
        self.assertGreater(total2, 70000)  # Exceeded budget!
        over_amount = total2 - 70000
        self.assertEqual(over_amount, 5000)

        # Undo/Delete expense 2
        self.conn.execute("DELETE FROM expenses WHERE id = 2 AND user_id = ?", (user_id,))
        cursor = self.conn.execute(
            "SELECT SUM(amount) FROM expenses WHERE user_id = ? AND created_at LIKE ?",
            (user_id, f"{today}%"),
        )
        total3 = cursor.fetchone()[0]
        self.assertEqual(total3, 45000)

    def test_task_lifecycle_and_snooze(self):
        user_id = 999
        tomorrow = "2026-11-15"

        # Multi-task batch creation (nightly plan)
        tasks = ["Buy bread", "Finish report", "Gym workout"]
        for t in tasks:
            self.conn.execute(
                "INSERT INTO tasks (user_id, task_text, target_date, remind_at, is_completed, created_at) VALUES (?, ?, ?, NULL, 0, ?)",
                (user_id, t, tomorrow, "2026-11-14 21:00:00"),
            )

        cursor = self.conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE user_id = ? AND target_date = ?",
            (user_id, tomorrow),
        )
        self.assertEqual(cursor.fetchone()[0], 3)

        # Add timed reminder
        self.conn.execute(
            "INSERT INTO tasks (user_id, task_text, target_date, remind_at, is_completed, created_at) VALUES (?, ?, ?, ?, 0, ?)",
            (user_id, "Call boss", tomorrow, "2026-11-15 14:00:00", "2026-11-14 21:05:00"),
        )

        # Snooze +15 minutes
        new_remind_time = "2026-11-15 14:15:00"
        self.conn.execute(
            "UPDATE tasks SET remind_at = ?, is_completed = 0 WHERE task_text = ?",
            (new_remind_time, "Call boss"),
        )
        row = self.conn.execute(
            "SELECT remind_at FROM tasks WHERE task_text = ?", ("Call boss",)
        ).fetchone()
        self.assertEqual(row["remind_at"], new_remind_time)

        # Mark done
        self.conn.execute(
            "UPDATE tasks SET is_completed = 1 WHERE task_text = ?", ("Call boss",)
        )
        row = self.conn.execute(
            "SELECT is_completed FROM tasks WHERE task_text = ?", ("Call boss",)
        ).fetchone()
        self.assertEqual(row["is_completed"], 1)

        # Persistence query on startup: should only return uncompleted reminders
        cursor = self.conn.execute(
            "SELECT id, task_text FROM tasks WHERE is_completed = 0 AND remind_at IS NOT NULL"
        )
        active_reminders = cursor.fetchall()
        self.assertEqual(len(active_reminders), 0)  # Call boss is marked done, so 0 active


class TestSecurityMiddleware(unittest.IsolatedAsyncioTestCase):
    async def test_admin_allowed(self):
        from middlewares import AdminMiddleware
        from unittest.mock import AsyncMock

        admin_id = 12345
        mw = AdminMiddleware(admin_user_id=admin_id)

        user_mock = MagicMock()
        user_mock.id = admin_id

        data = {"event_from_user": user_mock}
        event_mock = MockMessage()
        handler = AsyncMock(return_value="OK")

        result = await mw(handler, event_mock, data)
        self.assertEqual(result, "OK")
        handler.assert_awaited_once_with(event_mock, data)

    async def test_unauthorized_user_blocked(self):
        from middlewares import AdminMiddleware
        from unittest.mock import AsyncMock

        admin_id = 12345
        mw = AdminMiddleware(admin_user_id=admin_id)

        unauthorized_user = MagicMock()
        unauthorized_user.id = 99999
        unauthorized_user.username = "intruder"

        data = {"event_from_user": unauthorized_user}
        event_mock = MockMessage()
        event_mock.answer = AsyncMock()
        handler = AsyncMock(return_value="OK")

        result = await mw(handler, event_mock, data)
        self.assertIsNone(result)
        handler.assert_not_awaited()
        event_mock.answer.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
