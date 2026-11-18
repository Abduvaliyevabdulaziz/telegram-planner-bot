"""
Asynchronous Database Manager supporting both:
1. Cloud PostgreSQL (via asyncpg) for permanent persistence across cloud/Render restarts.
2. Local SQLite (via aiosqlite) for offline/local development.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
import aiosqlite

try:
    import asyncpg
except ImportError:
    asyncpg = None

from config import settings, format_dt, get_now, get_today_str

logger = logging.getLogger(__name__)


class Database:
    def __init__(
        self,
        db_path: Path | str | None = None,
        database_url: str | None = None,
    ) -> None:
        self.db_url = (database_url or getattr(settings, "DATABASE_URL", "") or "").strip()
        self.is_postgres = bool(
            self.db_url and (
                self.db_url.startswith("postgres://") or self.db_url.startswith("postgresql://")
            )
        )
        self.db_path = str(db_path or settings.DB_PATH)
        self.pool: Any = None
        self.cloud_sync: Any = None

    def set_cloud_sync(self, cloud_sync: Any) -> None:
        """Attach CloudSyncManager to automatically backup SQLite changes."""
        self.cloud_sync = cloud_sync

    def _trigger_sync(self) -> None:
        """Schedule a background sync to Telegram cloud storage if using SQLite."""
        if not self.is_postgres and self.cloud_sync is not None:
            self.cloud_sync.schedule_sync()

    async def init_db(self) -> None:
        """Create tables and indexes if they do not exist."""
        if self.is_postgres:
            if asyncpg is None:
                raise RuntimeError("asyncpg is required for PostgreSQL support. Please install asyncpg.")
            url = self.db_url
            if url.startswith("postgres://"):
                url = "postgresql://" + url[len("postgres://"):]
            logger.info("Connecting to PostgreSQL database pool...")
            self.pool = await asyncpg.create_pool(url, min_size=1, max_size=10)
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS expenses (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        amount BIGINT NOT NULL,
                        category TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_expenses_user_created 
                    ON expenses(user_id, created_at);

                    CREATE TABLE IF NOT EXISTS tasks (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        task_text TEXT NOT NULL,
                        target_date TEXT NOT NULL,
                        remind_at TEXT,
                        is_completed INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_tasks_user_target 
                    ON tasks(user_id, target_date);
                    CREATE INDEX IF NOT EXISTS idx_tasks_remind 
                    ON tasks(is_completed, remind_at);
                    """
                )
            logger.info("PostgreSQL database initialized successfully.")
        else:
            logger.info(f"Connecting to SQLite database at {self.db_path}...")
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA journal_mode=WAL;")
                await db.execute("PRAGMA foreign_keys=ON;")

                # Expenses table
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS expenses (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        amount INTEGER NOT NULL,
                        category TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_expenses_user_created 
                    ON expenses(user_id, created_at);
                    """
                )

                # Tasks table
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tasks (
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
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_tasks_user_target 
                    ON tasks(user_id, target_date);
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_tasks_remind 
                    ON tasks(is_completed, remind_at);
                    """
                )
                await db.commit()
            logger.info("SQLite database initialized successfully.")

    async def close(self) -> None:
        """Close connection pool if PostgreSQL."""
        if self.is_postgres and self.pool is not None:
            await self.pool.close()
            logger.info("PostgreSQL pool closed.")

    # =========================================================================
    # Expenses Operations
    # =========================================================================

    async def add_expense(
        self, user_id: int, amount: int, category: str, created_at: str | None = None
    ) -> int:
        """Add a new expense entry and return its ID."""
        ts = created_at or format_dt(get_now())
        if self.is_postgres:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO expenses (user_id, amount, category, created_at)
                    VALUES ($1, $2, $3, $4)
                    RETURNING id
                    """,
                    user_id, int(amount), category.strip(), ts,
                )
                return row["id"]
        else:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    """
                    INSERT INTO expenses (user_id, amount, category, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user_id, int(amount), category.strip(), ts),
                )
                await db.commit()
                row_id = cursor.lastrowid
            self._trigger_sync()
            return row_id

    async def delete_expense(self, expense_id: int, user_id: int) -> bool:
        """Delete an expense by ID if it belongs to the user."""
        if self.is_postgres:
            async with self.pool.acquire() as conn:
                res = await conn.execute(
                    "DELETE FROM expenses WHERE id = $1 AND user_id = $2",
                    expense_id, user_id,
                )
                return res != "DELETE 0"
        else:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "DELETE FROM expenses WHERE id = ? AND user_id = ?",
                    (expense_id, user_id),
                )
                await db.commit()
                deleted = cursor.rowcount > 0
            if deleted:
                self._trigger_sync()
            return deleted

    async def get_expense_by_id(self, expense_id: int) -> dict[str, Any] | None:
        """Fetch a single expense by its ID."""
        if self.is_postgres:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id, user_id, amount, category, created_at FROM expenses WHERE id = $1",
                    expense_id,
                )
                return dict(row) if row else None
        else:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT id, user_id, amount, category, created_at FROM expenses WHERE id = ?",
                    (expense_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                    return dict(row) if row else None

    async def get_expenses_sum_for_date(self, user_id: int, date_str: str) -> int:
        """Calculate total amount spent on a specific date (YYYY-MM-DD)."""
        pattern = f"{date_str}%"
        if self.is_postgres:
            async with self.pool.acquire() as conn:
                val = await conn.fetchval(
                    """
                    SELECT COALESCE(SUM(amount), 0)
                    FROM expenses
                    WHERE user_id = $1 AND created_at LIKE $2
                    """,
                    user_id, pattern,
                )
                return int(val) if val else 0
        else:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute(
                    """
                    SELECT COALESCE(SUM(amount), 0)
                    FROM expenses
                    WHERE user_id = ? AND created_at LIKE ?
                    """,
                    (user_id, pattern),
                ) as cursor:
                    row = await cursor.fetchone()
                    return int(row[0]) if row else 0

    async def get_expense_stats_by_period(
        self, user_id: int, start_dt: str, end_dt: str
    ) -> dict[str, Any]:
        """
        Calculate total sum, category breakdown with percentages,
        and last 10 individual transactions within a time period.
        """
        if self.is_postgres:
            async with self.pool.acquire() as conn:
                sum_row = await conn.fetchrow(
                    """
                    SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS count
                    FROM expenses
                    WHERE user_id = $1 AND created_at >= $2 AND created_at <= $3
                    """,
                    user_id, start_dt, end_dt,
                )
                total = int(sum_row["total"]) if sum_row else 0
                count = int(sum_row["count"]) if sum_row else 0

                categories: list[dict[str, Any]] = []
                if total > 0:
                    rows = await conn.fetch(
                        """
                        SELECT category, SUM(amount) AS cat_total, COUNT(*) AS cat_count
                        FROM expenses
                        WHERE user_id = $1 AND created_at >= $2 AND created_at <= $3
                        GROUP BY category
                        ORDER BY cat_total DESC
                        """,
                        user_id, start_dt, end_dt,
                    )
                    for r in rows:
                        cat_amount = int(r["cat_total"])
                        percentage = round((cat_amount / total) * 100, 1)
                        categories.append(
                            {
                                "category": r["category"],
                                "amount": cat_amount,
                                "count": int(r["cat_count"]),
                                "percentage": percentage,
                            }
                        )

                recent_rows = await conn.fetch(
                    """
                    SELECT id, amount, category, created_at
                    FROM expenses
                    WHERE user_id = $1 AND created_at >= $2 AND created_at <= $3
                    ORDER BY created_at DESC, id DESC
                    LIMIT 10
                    """,
                    user_id, start_dt, end_dt,
                )
                recent = [dict(r) for r in recent_rows]

                return {
                    "total": total,
                    "count": count,
                    "categories": categories,
                    "recent_transactions": recent,
                }
        else:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row

                # 1. Total sum and count
                async with db.execute(
                    """
                    SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS count
                    FROM expenses
                    WHERE user_id = ? AND created_at >= ? AND created_at <= ?
                    """,
                    (user_id, start_dt, end_dt),
                ) as cursor:
                    sum_row = await cursor.fetchone()
                    total = int(sum_row["total"]) if sum_row else 0
                    count = int(sum_row["count"]) if sum_row else 0

                # 2. Grouped breakdown by category
                categories: list[dict[str, Any]] = []
                if total > 0:
                    async with db.execute(
                        """
                        SELECT category, SUM(amount) AS cat_total, COUNT(*) AS cat_count
                        FROM expenses
                        WHERE user_id = ? AND created_at >= ? AND created_at <= ?
                        GROUP BY category
                        ORDER BY cat_total DESC
                        """,
                        (user_id, start_dt, end_dt),
                    ) as cursor:
                        rows = await cursor.fetchall()
                        for r in rows:
                            cat_amount = int(r["cat_total"])
                            percentage = round((cat_amount / total) * 100, 1)
                            categories.append(
                                {
                                    "category": r["category"],
                                    "amount": cat_amount,
                                    "count": int(r["cat_count"]),
                                    "percentage": percentage,
                                }
                            )

                # 3. Last 10 transactions
                recent: list[dict[str, Any]] = []
                async with db.execute(
                    """
                    SELECT id, amount, category, created_at
                    FROM expenses
                    WHERE user_id = ? AND created_at >= ? AND created_at <= ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT 10
                    """,
                    (user_id, start_dt, end_dt),
                ) as cursor:
                    recent_rows = await cursor.fetchall()
                    recent = [dict(r) for r in recent_rows]

                return {
                    "total": total,
                    "count": count,
                    "categories": categories,
                    "recent_transactions": recent,
                }

    async def get_all_expenses(self, user_id: int) -> list[dict[str, Any]]:
        """Retrieve all expenses for CSV export."""
        if self.is_postgres:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, created_at, amount, category
                    FROM expenses
                    WHERE user_id = $1
                    ORDER BY created_at ASC, id ASC
                    """,
                    user_id,
                )
                return [dict(r) for r in rows]
        else:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    """
                    SELECT id, created_at, amount, category
                    FROM expenses
                    WHERE user_id = ?
                    ORDER BY created_at ASC, id ASC
                    """,
                    (user_id,),
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(r) for r in rows]

    # =========================================================================
    # Tasks Operations
    # =========================================================================

    async def add_task(
        self,
        user_id: int,
        task_text: str,
        target_date: str,
        remind_at: str | None = None,
    ) -> int:
        """Add a single task and return its ID."""
        ts = format_dt(get_now())
        if self.is_postgres:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO tasks (user_id, task_text, target_date, remind_at, is_completed, created_at)
                    VALUES ($1, $2, $3, $4, 0, $5)
                    RETURNING id
                    """,
                    user_id, task_text.strip(), target_date, remind_at, ts,
                )
                return row["id"]
        else:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    """
                    INSERT INTO tasks (user_id, task_text, target_date, remind_at, is_completed, created_at)
                    VALUES (?, ?, ?, ?, 0, ?)
                    """,
                    (user_id, task_text.strip(), target_date, remind_at, ts),
                )
                await db.commit()
                task_id = cursor.lastrowid
            self._trigger_sync()
            return task_id

    async def add_multiple_tasks(
        self, user_id: int, task_texts: list[str], target_date: str
    ) -> list[int]:
        """Add multiple tasks for a target date (e.g., from the nightly planning prompt)."""
        ts = format_dt(get_now())
        task_ids: list[int] = []
        if self.is_postgres:
            async with self.pool.acquire() as conn:
                for text in task_texts:
                    cleaned = text.strip()
                    if not cleaned:
                        continue
                    row = await conn.fetchrow(
                        """
                        INSERT INTO tasks (user_id, task_text, target_date, remind_at, is_completed, created_at)
                        VALUES ($1, $2, $3, NULL, 0, $4)
                        RETURNING id
                        """,
                        user_id, cleaned, target_date, ts,
                    )
                    task_ids.append(row["id"])
            return task_ids
        else:
            async with aiosqlite.connect(self.db_path) as db:
                for text in task_texts:
                    cleaned = text.strip()
                    if not cleaned:
                        continue
                    cursor = await db.execute(
                        """
                        INSERT INTO tasks (user_id, task_text, target_date, remind_at, is_completed, created_at)
                        VALUES (?, ?, ?, NULL, 0, ?)
                        """,
                        (user_id, cleaned, target_date, ts),
                    )
                    task_ids.append(cursor.lastrowid)
                await db.commit()
            self._trigger_sync()
            return task_ids

    async def get_task_by_id(self, task_id: int) -> dict[str, Any] | None:
        """Fetch a single task by ID."""
        if self.is_postgres:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, user_id, task_text, target_date, remind_at, is_completed, created_at
                    FROM tasks
                    WHERE id = $1
                    """,
                    task_id,
                )
                return dict(row) if row else None
        else:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    """
                    SELECT id, user_id, task_text, target_date, remind_at, is_completed, created_at
                    FROM tasks
                    WHERE id = ?
                    """,
                    (task_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                    return dict(row) if row else None

    async def mark_task_completed(self, task_id: int, user_id: int) -> bool:
        """Mark a task as completed."""
        if self.is_postgres:
            async with self.pool.acquire() as conn:
                res = await conn.execute(
                    "UPDATE tasks SET is_completed = 1 WHERE id = $1 AND user_id = $2",
                    task_id, user_id,
                )
                return res != "UPDATE 0"
        else:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "UPDATE tasks SET is_completed = 1 WHERE id = ? AND user_id = ?",
                    (task_id, user_id),
                )
                await db.commit()
                ok = cursor.rowcount > 0
            if ok:
                self._trigger_sync()
            return ok

    async def toggle_task_status(self, task_id: int, user_id: int) -> dict[str, Any] | None:
        """Toggle task completion between 0 and 1."""
        if self.is_postgres:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT is_completed FROM tasks WHERE id = $1 AND user_id = $2",
                    task_id, user_id,
                )
                if not row:
                    return None
                new_status = 0 if row["is_completed"] == 1 else 1

                await conn.execute(
                    "UPDATE tasks SET is_completed = $1 WHERE id = $2 AND user_id = $3",
                    new_status, task_id, user_id,
                )
                updated_row = await conn.fetchrow(
                    "SELECT id, user_id, task_text, target_date, remind_at, is_completed FROM tasks WHERE id = $1",
                    task_id,
                )
                return dict(updated_row) if updated_row else None
        else:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT is_completed FROM tasks WHERE id = ? AND user_id = ?",
                    (task_id, user_id),
                ) as cursor:
                    row = await cursor.fetchone()
                    if not row:
                        return None
                    new_status = 0 if row["is_completed"] == 1 else 1

                await db.execute(
                    "UPDATE tasks SET is_completed = ? WHERE id = ? AND user_id = ?",
                    (new_status, task_id, user_id),
                )
                await db.commit()

                async with db.execute(
                    "SELECT id, user_id, task_text, target_date, remind_at, is_completed FROM tasks WHERE id = ?",
                    (task_id,),
                ) as cursor:
                    updated_row = await cursor.fetchone()
            if updated_row:
                self._trigger_sync()
            return dict(updated_row) if updated_row else None

    async def delete_task(self, task_id: int, user_id: int) -> bool:
        """Delete a task by ID."""
        if self.is_postgres:
            async with self.pool.acquire() as conn:
                res = await conn.execute(
                    "DELETE FROM tasks WHERE id = $1 AND user_id = $2",
                    task_id, user_id,
                )
                return res != "DELETE 0"
        else:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "DELETE FROM tasks WHERE id = ? AND user_id = ?",
                    (task_id, user_id),
                )
                await db.commit()
                deleted = cursor.rowcount > 0
            if deleted:
                self._trigger_sync()
            return deleted

    async def reschedule_reminder(self, task_id: int, new_remind_at: str) -> bool:
        """Reschedule a task's reminder timestamp and reset completion status."""
        if self.is_postgres:
            async with self.pool.acquire() as conn:
                res = await conn.execute(
                    "UPDATE tasks SET remind_at = $1, is_completed = 0 WHERE id = $2",
                    new_remind_at, task_id,
                )
                return res != "UPDATE 0"
        else:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    """
                    UPDATE tasks
                    SET remind_at = ?, is_completed = 0
                    WHERE id = ?
                    """,
                    (new_remind_at, task_id),
                )
                await db.commit()
                ok = cursor.rowcount > 0
            if ok:
                self._trigger_sync()
            return ok

    async def rollover_uncompleted_tasks(self, user_id: int, today_str: str) -> int:
        """
        Roll over uncompleted tasks from past dates into today.
        Guarantees that pending to-dos are never lost when dates change.
        Also cleans up any accidental tasks created from reply menu button taps.
        """
        cleanup_query_pg = """
            DELETE FROM tasks
            WHERE user_id = $1 AND task_text IN ('📅 My Planner', '💸 Expenses', '➕ Quick Todo', '📊 Today''s Summary')
        """
        cleanup_query_sqlite = """
            DELETE FROM tasks
            WHERE user_id = ? AND task_text IN ('📅 My Planner', '💸 Expenses', '➕ Quick Todo', '📊 Today''s Summary')
        """
        if self.is_postgres:
            async with self.pool.acquire() as conn:
                await conn.execute(cleanup_query_pg, user_id)
                res = await conn.execute(
                    """
                    UPDATE tasks
                    SET target_date = $1
                    WHERE user_id = $2 AND target_date < $1 AND is_completed = 0
                    """,
                    today_str, user_id,
                )
                count = int(res.split()[1]) if " " in res else 0
                if count > 0:
                    self._trigger_sync()
                return count
        else:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(cleanup_query_sqlite, (user_id,))
                cursor = await db.execute(
                    """
                    UPDATE tasks
                    SET target_date = ?
                    WHERE user_id = ? AND target_date < ? AND is_completed = 0
                    """,
                    (today_str, user_id, today_str),
                )
                await db.commit()
                count = cursor.rowcount
            if count > 0:
                self._trigger_sync()
            return count

    async def get_tasks_for_date(self, user_id: int, target_date: str) -> list[dict[str, Any]]:
        """Fetch all tasks planned for a specific date (YYYY-MM-DD)."""
        today_str = get_today_str()
        if target_date == today_str:
            await self.rollover_uncompleted_tasks(user_id, today_str)

        if self.is_postgres:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, user_id, task_text, target_date, remind_at, is_completed, created_at
                    FROM tasks
                    WHERE user_id = $1 AND target_date = $2
                    ORDER BY is_completed ASC, remind_at ASC NULLS LAST, id ASC
                    """,
                    user_id, target_date,
                )
                return [dict(r) for r in rows]
        else:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    """
                    SELECT id, user_id, task_text, target_date, remind_at, is_completed, created_at
                    FROM tasks
                    WHERE user_id = ? AND target_date = ?
                    ORDER BY is_completed ASC, remind_at ASC NULLS LAST, id ASC
                    """,
                    (user_id, target_date),
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(r) for r in rows]

    async def get_scheduled_reminders_for_date(
        self, user_id: int, target_date: str
    ) -> list[dict[str, Any]]:
        """Fetch all tasks with a reminder set for target_date."""
        pattern = f"{target_date}%"
        if self.is_postgres:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, user_id, task_text, target_date, remind_at, is_completed
                    FROM tasks
                    WHERE user_id = $1 AND remind_at LIKE $2
                    ORDER BY remind_at ASC, id ASC
                    """,
                    user_id, pattern,
                )
                return [dict(r) for r in rows]
        else:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    """
                    SELECT id, user_id, task_text, target_date, remind_at, is_completed
                    FROM tasks
                    WHERE user_id = ? AND remind_at LIKE ?
                    ORDER BY remind_at ASC, id ASC
                    """,
                    (user_id, pattern),
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(r) for r in rows]

    async def get_all_active_future_reminders(self) -> list[dict[str, Any]]:
        """
        Fetch all uncompleted tasks with a remind_at timestamp.
        Used on bot startup to re-register APScheduler jobs across restarts.
        """
        if self.is_postgres:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, user_id, task_text, target_date, remind_at, is_completed
                    FROM tasks
                    WHERE is_completed = 0 AND remind_at IS NOT NULL
                    ORDER BY remind_at ASC
                    """
                )
                return [dict(r) for r in rows]
        else:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    """
                    SELECT id, user_id, task_text, target_date, remind_at, is_completed
                    FROM tasks
                    WHERE is_completed = 0 AND remind_at IS NOT NULL
                    ORDER BY remind_at ASC
                    """
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(r) for r in rows]

    async def get_planner_dashboard(
        self, user_id: int, today_str: str, tomorrow_str: str, now_dt_str: str
    ) -> dict[str, Any]:
        """Fetch all data needed for the interactive planner menu."""
        today_tasks = await self.get_tasks_for_date(user_id, today_str)
        tomorrow_tasks = await self.get_tasks_for_date(user_id, tomorrow_str)

        if self.is_postgres:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, user_id, task_text, target_date, remind_at, is_completed
                    FROM tasks
                    WHERE user_id = $1 AND is_completed = 0 AND remind_at >= $2
                    ORDER BY remind_at ASC
                    LIMIT 10
                    """,
                    user_id, now_dt_str,
                )
                upcoming_reminders = [dict(r) for r in rows]
        else:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    """
                    SELECT id, user_id, task_text, target_date, remind_at, is_completed
                    FROM tasks
                    WHERE user_id = ? AND is_completed = 0 AND remind_at >= ?
                    ORDER BY remind_at ASC
                    LIMIT 10
                    """,
                    (user_id, now_dt_str),
                ) as cursor:
                    rows = await cursor.fetchall()
                    upcoming_reminders = [dict(r) for r in rows]

        return {
            "today_tasks": today_tasks,
            "tomorrow_tasks": tomorrow_tasks,
            "upcoming_reminders": upcoming_reminders,
        }
