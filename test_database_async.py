"""
Async Database Integration Test with real aiosqlite.
"""
import asyncio
import os
import unittest
from pathlib import Path
from database import Database

TEST_DB_PATH = Path("test_async_integration.db")


class TestAsyncDatabase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if TEST_DB_PATH.exists():
            TEST_DB_PATH.unlink()
        self.db = Database(TEST_DB_PATH)
        await self.db.init_db()

    async def asyncTearDown(self):
        if TEST_DB_PATH.exists():
            TEST_DB_PATH.unlink()

    async def test_full_database_lifecycle(self):
        user_id = 12345
        today = "2026-11-14"

        # 1. Expenses: Add expense
        exp1_id = await self.db.add_expense(
            user_id, 45000, "Fast food", f"{today} 13:00:00"
        )
        self.assertGreater(exp1_id, 0)

        exp2_id = await self.db.add_expense(
            user_id, 30000, "taxi", f"{today} 15:30:00"
        )
        self.assertGreater(exp2_id, 0)

        # 2. Check today's sum (45000 + 30000 = 75000 UZS)
        today_total = await self.db.get_expenses_sum_for_date(user_id, today)
        self.assertEqual(today_total, 75000)

        # 3. Stats & Breakdown
        stats = await self.db.get_expense_stats_by_period(
            user_id, f"{today} 00:00:00", f"{today} 23:59:59"
        )
        self.assertEqual(stats["total"], 75000)
        self.assertEqual(stats["count"], 2)
        self.assertEqual(len(stats["categories"]), 2)
        self.assertEqual(stats["categories"][0]["category"], "Fast food")
        self.assertEqual(stats["categories"][0]["amount"], 45000)
        self.assertEqual(stats["categories"][0]["percentage"], 60.0)

        # 4. Undo / Delete
        deleted = await self.db.delete_expense(exp2_id, user_id)
        self.assertTrue(deleted)
        new_total = await self.db.get_expenses_sum_for_date(user_id, today)
        self.assertEqual(new_total, 45000)

        # 5. Tasks: Batch add tomorrow's tasks (21:00 evening loop)
        tomorrow = "2026-11-15"
        task_texts = ["Review sprint backlog", "Call client", "Hit the gym"]
        task_ids = await self.db.add_multiple_tasks(user_id, task_texts, tomorrow)
        self.assertEqual(len(task_ids), 3)

        tasks = await self.db.get_tasks_for_date(user_id, tomorrow)
        self.assertEqual(len(tasks), 3)
        self.assertEqual(tasks[0]["task_text"], "Review sprint backlog")
        self.assertEqual(tasks[0]["is_completed"], 0)

        # 6. Mark task done
        marked = await self.db.mark_task_completed(task_ids[0], user_id)
        self.assertTrue(marked)
        updated_tasks = await self.db.get_tasks_for_date(user_id, tomorrow)
        completed_task = next(t for t in updated_tasks if t["id"] == task_ids[0])
        self.assertEqual(completed_task["is_completed"], 1)

        # 7. Timed Reminders & Snooze
        remind_time = f"{tomorrow} 18:30:00"
        remind_id = await self.db.add_task(
            user_id, "Prepare dinner", tomorrow, remind_time
        )
        all_reminders = await self.db.get_all_active_future_reminders()
        self.assertEqual(len(all_reminders), 1)
        self.assertEqual(all_reminders[0]["task_text"], "Prepare dinner")

        # Snooze +15 minutes
        snoozed_time = f"{tomorrow} 18:45:00"
        snoozed = await self.db.reschedule_reminder(remind_id, snoozed_time)
        self.assertTrue(snoozed)

        task_after_snooze = await self.db.get_task_by_id(remind_id)
        self.assertEqual(task_after_snooze["remind_at"], snoozed_time)

    async def test_rollover_uncompleted_tasks(self):
        user_id = 999
        yesterday = "2026-11-13"
        today = "2026-11-14"

        # Add uncompleted task for yesterday
        task_id = await self.db.add_task(user_id, "Old uncompleted task", yesterday)
        # Add completed task for yesterday
        completed_id = await self.db.add_task(user_id, "Old completed task", yesterday)
        await self.db.mark_task_completed(completed_id, user_id)

        # Roll over
        count = await self.db.rollover_uncompleted_tasks(user_id, today)
        self.assertEqual(count, 1)

        # Verify task is now on today's list
        today_tasks = await self.db.get_tasks_for_date(user_id, today)
        self.assertEqual(len(today_tasks), 1)
        self.assertEqual(today_tasks[0]["task_text"], "Old uncompleted task")

        # Verify completed task remained on yesterday
        yesterday_tasks = await self.db.get_tasks_for_date(user_id, yesterday)
        self.assertEqual(len(yesterday_tasks), 1)
        self.assertEqual(yesterday_tasks[0]["task_text"], "Old completed task")


if __name__ == "__main__":
    unittest.main()
