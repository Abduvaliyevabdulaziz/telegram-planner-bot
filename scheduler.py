"""
APScheduler configuration and background job definitions.
Configured strictly to Asia/Tashkent (UTC+5) timezone.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from aiogram import Bot

from config import (
    TIMEZONE,
    settings,
    get_now,
    get_today_str,
    get_yesterday_str,
    format_currency,
)
from database import Database
from keyboards import (
    get_night_plan_prompt_keyboard,
    get_morning_briefing_keyboard,
    get_reminder_alert_keyboard,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Job Functions
# =============================================================================

async def send_nightly_plan_prompt(bot: Bot, admin_user_id: int) -> None:
    """
    21:00 (9:00 PM Tashkent time) automated prompt.
    Prompts the user to write their plan and main goals for tomorrow.
    """
    logger.info("Executing 21:00 Nightly Planning Prompt job...")
    text = (
        "🌙 <b>Good evening! Time to plan tomorrow.</b>\n\n"
        "What are your main tasks and goals for tomorrow?\n"
        "Tap the button below to start entering your tasks."
    )
    try:
        await bot.send_message(
            chat_id=admin_user_id,
            text=text,
            reply_markup=get_night_plan_prompt_keyboard(),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Failed to send 21:00 nightly prompt: {e}", exc_info=True)


async def send_morning_briefing(bot: Bot, db: Database, admin_user_id: int) -> None:
    """
    09:00 (9:00 AM Tashkent time) automated briefing.
    Includes:
      1. Today's Plan (tasks written the night before or added earlier)
      2. Scheduled Reminders for today
      3. Yesterday's Spending Summary (vs 70,000 UZS daily limit)
    """
    logger.info("Executing 09:00 Morning Briefing job...")
    today_str = get_today_str()
    yesterday_str = get_yesterday_str()

    try:
        # 1. Fetch Today's Tasks
        today_tasks = await db.get_tasks_for_date(admin_user_id, today_str)

        # 2. Fetch Scheduled Reminders for today
        reminders = await db.get_scheduled_reminders_for_date(admin_user_id, today_str)

        # 3. Fetch Yesterday's Spending
        yesterday_spent = await db.get_expenses_sum_for_date(admin_user_id, yesterday_str)

        # Build message
        lines = [
            f"🌅 <b>Good Morning! Daily Briefing</b>",
            f"📅 <i>{today_str} (Asia/Tashkent)</i>\n",
            "📋 <b>Today's Plan:</b>",
        ]

        if not today_tasks:
            lines.append("<i>No tasks scheduled for today. Use /todo or /plan to add some!</i>")
        else:
            for i, task in enumerate(today_tasks, start=1):
                status_icon = "✅" if task["is_completed"] == 1 else "⬜"
                lines.append(f"{status_icon} {i}. {task['task_text']}")

        lines.append("\n⏰ <b>Scheduled Timed Reminders:</b>")
        if not reminders:
            lines.append("<i>No timed reminders set for today.</i>")
        else:
            for r in reminders:
                time_part = r["remind_at"][11:16] if r["remind_at"] else "??:??"
                status = "✅" if r["is_completed"] == 1 else "⏳"
                lines.append(f"{status} <b>{time_part}</b> — {r['task_text']}")

        lines.append(f"\n💸 <b>Yesterday's Spending Summary ({yesterday_str}):</b>")
        spent_fmt = format_currency(yesterday_spent)
        limit_fmt = format_currency(settings.DAILY_BUDGET_LIMIT)

        if yesterday_spent <= settings.DAILY_BUDGET_LIMIT:
            remaining = settings.DAILY_BUDGET_LIMIT - yesterday_spent
            rem_fmt = format_currency(remaining)
            lines.append(
                f"• Total Spent: <b>{spent_fmt}</b>\n"
                f"✅ <i>Great job! Under the {limit_fmt} limit by {rem_fmt}.</i> 👏"
            )
        else:
            over = yesterday_spent - settings.DAILY_BUDGET_LIMIT
            over_fmt = format_currency(over)
            lines.append(
                f"• Total Spent: <b>{spent_fmt}</b>\n"
                f"⚠️ <i>Exceeded daily {limit_fmt} limit by +{over_fmt}.</i> 🚨"
            )

        message_text = "\n".join(lines)
        keyboard = get_morning_briefing_keyboard(today_tasks)

        await bot.send_message(
            chat_id=admin_user_id,
            text=message_text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Failed to send 09:00 morning briefing: {e}", exc_info=True)


async def send_reminder_alert(
    bot: Bot, db: Database, task_id: int, admin_user_id: int
) -> None:
    """
    Triggered when a task's remind_at timestamp is reached.
    Sends: 🔔 REMINDER: [Task Text] with Done and Snooze buttons.
    """
    try:
        task = await db.get_task_by_id(task_id)
        if not task:
            logger.info(f"Reminder skipped: task_id={task_id} not found.")
            return

        # If already marked done, skip
        if task.get("is_completed", 0) == 1:
            logger.info(f"Reminder skipped: task_id={task_id} is already completed.")
            return

        text = f"🔔 <b>REMINDER:</b>\n\n📌 <b>{task['task_text']}</b>"
        await bot.send_message(
            chat_id=admin_user_id,
            text=text,
            reply_markup=get_reminder_alert_keyboard(task_id),
            parse_mode="HTML",
        )
        logger.info(f"Reminder alert successfully sent for task_id={task_id}")
        await db.clear_task_remind_at(task_id)
    except Exception as e:
        logger.error(f"Failed to deliver reminder for task_id={task_id}: {e}", exc_info=True)


async def flush_overdue_reminders(bot: Bot, db: Database, admin_user_id: int) -> int:
    """
    Checks for any uncompleted tasks whose reminder time has passed
    (e.g. while Render was sleeping or restarting) and delivers them immediately.
    """
    now = get_now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    overdue = await db.get_overdue_uncompleted_reminders(now_str)
    sent_count = 0
    for task in overdue:
        task_id = task["id"]
        try:
            remind_time = task.get("remind_at", "earlier")
            time_part = remind_time[11:16] if len(remind_time) >= 16 else remind_time
            text = (
                f"🔔 <b>REMINDER</b> <i>(Scheduled for {time_part})</i>:\n\n"
                f"📌 <b>{task['task_text']}</b>"
            )
            await bot.send_message(
                chat_id=admin_user_id,
                text=text,
                reply_markup=get_reminder_alert_keyboard(task_id),
                parse_mode="HTML",
            )
            await db.clear_task_remind_at(task_id)
            sent_count += 1
            logger.info(f"Overdue reminder delivered for task_id={task_id}")
        except Exception as e:
            logger.error(f"Failed delivering overdue reminder {task_id}: {e}")
    return sent_count


# =============================================================================
# Scheduler Lifecycle & Registration
# =============================================================================

def create_scheduler() -> AsyncIOScheduler:
    """Initialize APScheduler strictly configured with Asia/Tashkent timezone."""
    return AsyncIOScheduler(timezone=TIMEZONE)


def register_fixed_cron_jobs(
    scheduler: AsyncIOScheduler, bot: Bot, db: Database, admin_user_id: int
) -> None:
    """Register the fixed 21:00 evening and 09:00 morning cron jobs."""
    # 21:00 Evening Planning Prompt
    scheduler.add_job(
        send_nightly_plan_prompt,
        trigger=CronTrigger(
            hour=settings.EVENING_PLAN_HOUR,
            minute=settings.EVENING_PLAN_MINUTE,
            timezone=TIMEZONE,
        ),
        id="cron_nightly_plan_prompt",
        name="21:00 Evening Planning Prompt",
        replace_existing=True,
        misfire_grace_time=3600,
        args=[bot, admin_user_id],
    )

    # 09:00 Morning Briefing
    scheduler.add_job(
        send_morning_briefing,
        trigger=CronTrigger(
            hour=settings.MORNING_BRIEF_HOUR,
            minute=settings.MORNING_BRIEF_MINUTE,
            timezone=TIMEZONE,
        ),
        id="cron_morning_briefing",
        name="09:00 Morning Briefing",
        replace_existing=True,
        misfire_grace_time=3600,
        args=[bot, db, admin_user_id],
    )
    logger.info("Fixed cron jobs (21:00 Nightly and 09:00 Morning) registered.")


def schedule_task_reminder(
    scheduler: AsyncIOScheduler,
    bot: Bot,
    db: Database,
    task_id: int,
    remind_dt: datetime,
    admin_user_id: int,
) -> None:
    """Schedule or reschedule a one-shot reminder job in APScheduler."""
    job_id = f"task_reminder_{task_id}"

    # Ensure timezone awareness
    if remind_dt.tzinfo is None:
        remind_dt = remind_dt.replace(tzinfo=TIMEZONE)

    scheduler.add_job(
        send_reminder_alert,
        trigger=DateTrigger(run_date=remind_dt, timezone=TIMEZONE),
        id=job_id,
        name=f"Task Reminder {task_id}",
        replace_existing=True,
        misfire_grace_time=None,  # Never skip overdue reminders due to container suspension
        args=[bot, db, task_id, admin_user_id],
    )
    logger.info(f"Scheduled reminder job_id={job_id} at {remind_dt.isoformat()}")


def cancel_task_reminder(scheduler: AsyncIOScheduler, task_id: int) -> None:
    """Remove a reminder job from APScheduler if scheduled."""
    job_id = f"task_reminder_{task_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
        logger.info(f"Cancelled reminder job_id={job_id}")


async def restore_persisted_reminders(
    scheduler: AsyncIOScheduler, bot: Bot, db: Database, admin_user_id: int
) -> int:
    """
    On startup, load all uncompleted tasks with a remind_at timestamp
    and restore them into APScheduler.
    If a reminder's time passed while the bot was offline, fires within 5 seconds.
    """
    active_reminders = await db.get_all_active_future_reminders()
    now = get_now()
    count = 0

    for task in active_reminders:
        remind_at_str = task.get("remind_at")
        if not remind_at_str:
            continue

        try:
            remind_dt = datetime.strptime(remind_at_str, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=TIMEZONE
            )
        except ValueError:
            try:
                remind_dt = datetime.fromisoformat(remind_at_str)
                if remind_dt.tzinfo is None:
                    remind_dt = remind_dt.replace(tzinfo=TIMEZONE)
            except Exception:
                logger.warning(f"Could not parse remind_at '{remind_at_str}' for task {task['id']}")
                continue

        # If missed while offline, schedule to fire almost immediately (in 5s)
        if remind_dt <= now:
            fire_dt = now + timedelta(seconds=5)
            logger.info(
                f"Task {task['id']} reminder was missed during downtime ({remind_dt}). "
                f"Rescheduling for immediate execution at {fire_dt}."
            )
            schedule_task_reminder(
                scheduler, bot, db, task["id"], fire_dt, admin_user_id
            )
        else:
            schedule_task_reminder(
                scheduler, bot, db, task["id"], remind_dt, admin_user_id
            )
        count += 1

    logger.info(f"Restored {count} active reminder(s) from database into APScheduler.")
    return count
