"""
Smart Daily Planner & Reminders Router.
Handles 21:00 evening planning loop, 09:00 briefing callbacks,
timed reminders (/remind, /in), quick todos (/todo), snooze, and interactive dashboard (/plan).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from config import (
    TIMEZONE,
    settings,
    get_now,
    get_today_str,
    get_tomorrow_str,
    format_dt,
)
from database import Database
from keyboards import (
    get_night_plan_active_keyboard,
    get_planner_dashboard_keyboard,
    get_reminder_alert_keyboard,
    get_task_delete_keyboard,
    get_task_alert_selection_keyboard,
    get_main_reply_keyboard,
)
from scheduler import schedule_task_reminder, cancel_task_reminder

logger = logging.getLogger(__name__)

router = Router(name="planner_router")


# =============================================================================
# FSM States
# =============================================================================

class PlannerStates(StatesGroup):
    waiting_for_tomorrow_plan = State()
    waiting_for_quick_todo = State()
    waiting_for_single_today_task = State()
    waiting_for_single_tomorrow_task = State()
    waiting_for_task_alert_time = State()


MAIN_MENU_BUTTONS = {"💸 Expenses", "📅 My Planner", "➕ Quick Todo", "📊 Today's Summary"}

PLANNER_QUERIES = {
    "plan", "my plan", "planner", "my planner",
    "todo", "to do", "to-do", "todos", "to dos",
    "todo list", "to do list", "to-do list", "todos list",
    "tasks", "my tasks", "task list", "my task list",
    "today tasks", "today's tasks", "todays tasks",
    "what to do", "list", "my list"
}


def is_natural_todo_add(text: str | None) -> bool:
    """Matches natural todo creation like 'todo Buy milk', 'to-do: Call client'."""
    if not text:
        return False
    t = text.strip()
    low = t.lower()
    for prefix in ("todo: ", "todo ", "to-do: ", "to-do ", "to do: "):
        if low.startswith(prefix) and len(t) > len(prefix):
            return True
    return False


async def handle_button_breakout(message: Message, state: FSMContext, db: Database) -> bool:
    """If user taps a main menu button or command while in an FSM state, clear state and handle immediately."""
    text = (message.text or "").strip()
    if text in MAIN_MENU_BUTTONS or text.startswith("/"):
        await state.clear()
        if text == "📅 My Planner" or text == "/plan":
            text_resp, keyboard = await build_planner_dashboard_message(message.from_user.id, db)
            await message.answer(text_resp, reply_markup=keyboard, parse_mode="HTML")
            return True
        elif text == "➕ Quick Todo":
            await state.set_state(PlannerStates.waiting_for_quick_todo)
            await message.answer(
                "✍️ <b>Enter task text for today:</b>\n<i>(Send your task description, or /cancel to abort)</i>",
                parse_mode="HTML",
            )
            return True
        elif text == "📊 Today's Summary":
            from handlers.common import handle_todays_summary
            await handle_todays_summary(message, db)
            return True
        elif text == "💸 Expenses":
            from handlers.expenses import show_expenses_menu
            await show_expenses_menu(message)
            return True
        elif text == "/cancel":
            await message.answer("❌ Action cancelled.", reply_markup=get_main_reply_keyboard())
            return True
        return False
    return False


# =============================================================================
# Parsers for Reminders
# =============================================================================

def parse_relative_time(arg: str) -> tuple[timedelta, str] | None:
    """
    Parses relative time expressions:
      - '45m Check oven'
      - '2h Gym workout'
      - '1h30m Call team'
      - '1d Clean garage'
    """
    cleaned = arg.strip()
    pattern = r"^((?:\d+\s*(?:d|day|days|h|hr|hrs|hour|hours|m|min|mins|minute|minutes|s|sec|secs)\s*)+)\s+(.*)$"
    m = re.match(pattern, cleaned, re.IGNORECASE)
    if not m:
        return None

    time_part, task_text = m.groups()
    units = re.findall(r"(\d+)\s*([a-zA-Z]+)", time_part)
    if not units:
        return None

    total_seconds = 0
    for val_str, unit in units:
        val = int(val_str)
        u = unit.lower()
        if u in ("d", "day", "days"):
            total_seconds += val * 86400
        elif u in ("h", "hr", "hrs", "hour", "hours"):
            total_seconds += val * 3600
        elif u in ("m", "min", "mins", "minute", "minutes"):
            total_seconds += val * 60
        elif u in ("s", "sec", "secs"):
            total_seconds += val

    if total_seconds <= 0 or not task_text.strip():
        return None

    return timedelta(seconds=total_seconds), task_text.strip()


def parse_remind_command(arg: str, now: datetime) -> tuple[datetime, str] | None:
    """
    Parses specific time/date expressions:
      - '18:30 Call client' -> today or tomorrow at 18:30
      - '2026-11-20 14:00 Submit project' -> specific datetime
      - '45m Check oven' -> relative timer
    """
    cleaned = arg.strip()

    # Case 0: Relative duration (e.g. 45m, 2h, 1h30m)
    rel = parse_relative_time(cleaned)
    if rel:
        delta, task_text = rel
        return now + delta, task_text

    # Case 1: YYYY-MM-DD HH:MM task text
    m_full = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})\s+(.*)$", cleaned)
    if m_full:
        date_str, time_str, task_text = m_full.groups()
        try:
            dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M").replace(
                tzinfo=TIMEZONE
            )
            if not task_text.strip():
                return None
            return dt, task_text.strip()
        except ValueError:
            return None

    # Case 2: HH:MM task text
    m_time = re.match(r"^(\d{1,2}:\d{2})\s+(.*)$", cleaned)
    if m_time:
        time_str, task_text = m_time.groups()
        try:
            t = datetime.strptime(time_str, "%H:%M").time()
            target_dt = datetime.combine(now.date(), t, tzinfo=TIMEZONE)
            # If specified time already passed today, schedule for tomorrow
            if target_dt <= now:
                target_dt += timedelta(days=1)
            if not task_text.strip():
                return None
            return target_dt, task_text.strip()
        except ValueError:
            return None

    return None


async def build_planner_dashboard_message(user_id: int, db: Database) -> tuple[str, Any]:
    """Generates the interactive planner dashboard text and keyboard."""
    today_str = get_today_str()
    tomorrow_str = get_tomorrow_str()
    now = get_now()
    now_dt_str = format_dt(now)

    dashboard = await db.get_planner_dashboard(user_id, today_str, tomorrow_str, now_dt_str)
    today_tasks = dashboard["today_tasks"]
    tomorrow_tasks = dashboard["tomorrow_tasks"]
    upcoming_reminders = dashboard["upcoming_reminders"]

    lines = [
        "📅 <b>My Daily Planner Dashboard</b>",
        f"🕒 <i>Current Time: {now.strftime('%H:%M')} (Asia/Tashkent)</i>\n",
        f"📋 <b>Today's Tasks ({today_str}):</b>",
    ]

    if not today_tasks:
        lines.append("<i>• No tasks scheduled for today.</i>")
    else:
        for t in today_tasks:
            icon = "✅" if t["is_completed"] == 1 else "⬜"
            rem_str = f" [⏰ {t['remind_at'][11:16]}]" if t.get("remind_at") else ""
            lines.append(f"{icon} {t['task_text']}{rem_str}")

    lines.append(f"\n🔮 <b>Tomorrow's Tasks ({tomorrow_str}):</b>")
    if not tomorrow_tasks:
        lines.append("<i>• No tasks planned yet for tomorrow.</i>")
    else:
        for t in tomorrow_tasks:
            icon = "✅" if t["is_completed"] == 1 else "⬜"
            lines.append(f"{icon} {t['task_text']}")

    lines.append("\n⏰ <b>Upcoming Timed Alerts:</b>")
    if not upcoming_reminders:
        lines.append("<i>• No upcoming reminders scheduled.</i>")
    else:
        for r in upcoming_reminders[:5]:
            time_part = r["remind_at"][5:16]  # MM-DD HH:MM
            lines.append(f"• <b>{time_part}</b> — {r['task_text']}")

    text = "\n".join(lines)
    keyboard = get_planner_dashboard_keyboard(today_tasks, tomorrow_tasks)
    return text, keyboard


# =============================================================================
# Core Feature 2: 21:00 Nightly Planning Loop
# =============================================================================

@router.callback_query(F.data == "start_night_plan")
@router.message(Command("plan_tomorrow"))
async def start_tomorrow_planning(
    event: Message | CallbackQuery, state: FSMContext
) -> None:
    """Enter the evening planning state for tomorrow."""
    await state.set_state(PlannerStates.waiting_for_tomorrow_plan)
    await state.update_data(accumulated_tasks=[])

    tomorrow_str = get_tomorrow_str()
    text = (
        f"✍️ <b>Planning for Tomorrow ({tomorrow_str}):</b>\n\n"
        "• Send a multi-line message (each line will be saved as a separate task)\n"
        "• OR send tasks one by one in multiple messages.\n\n"
        "When finished, tap <b>✅ Save Plan</b> below."
    )

    keyboard = get_night_plan_active_keyboard()
    if isinstance(event, CallbackQuery):
        await event.answer()
        if event.message:
            await event.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.message(PlannerStates.waiting_for_tomorrow_plan, F.text)
async def handle_incoming_night_plan_text(
    message: Message, state: FSMContext, db: Database
) -> None:
    """Capture multi-line or individual task text for tomorrow."""
    if await handle_button_breakout(message, state, db):
        return

    text = message.text or ""
    lines = [
        line.strip() for line in text.split("\n")
        if line.strip() and line.strip() not in MAIN_MENU_BUTTONS and not line.strip().startswith("/")
    ]

    if not lines:
        return

    data = await state.get_data()
    current_tasks: list[str] = data.get("accumulated_tasks", [])
    current_tasks.extend(lines)
    await state.update_data(accumulated_tasks=current_tasks)

    tomorrow_str = get_tomorrow_str()
    preview_lines = [
        f"📝 <b>Tomorrow's Tasks ({tomorrow_str}):</b>",
    ]
    for i, t in enumerate(current_tasks, start=1):
        preview_lines.append(f"{i}. {t}")

    preview_lines.append(
        "\n<i>Send more tasks or tap <b>✅ Save Plan</b> when you are ready.</i>"
    )

    await message.answer(
        "\n".join(preview_lines),
        reply_markup=get_night_plan_active_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(PlannerStates.waiting_for_tomorrow_plan, F.data == "finish_night_plan")
async def finish_tomorrow_planning(
    callback: CallbackQuery, state: FSMContext, db: Database
) -> None:
    """Save all accumulated tasks to SQLite for tomorrow's date."""
    data = await state.get_data()
    tasks: list[str] = data.get("accumulated_tasks", [])
    user_id = callback.from_user.id
    tomorrow_str = get_tomorrow_str()

    if not tasks:
        await callback.answer("No tasks were entered yet.", show_alert=True)
        return

    # Batch save tasks in database
    await db.add_multiple_tasks(user_id=user_id, task_texts=tasks, target_date=tomorrow_str)
    await state.clear()

    confirm_lines = [
        f"🎉 <b>Tomorrow's Plan Saved Successfully!</b>",
        f"📅 Date: <b>{tomorrow_str}</b>",
        f"Total Tasks: <b>{len(tasks)}</b>\n",
    ]
    for i, t in enumerate(tasks, start=1):
        confirm_lines.append(f"⬜ {i}. {t}")

    confirm_lines.append(
        "\n<i>You will receive this in your 09:00 Morning Briefing tomorrow!</i>"
    )

    if callback.message:
        await callback.message.edit_text(
            "\n".join(confirm_lines),
            reply_markup=None,
            parse_mode="HTML",
        )
    await callback.answer("Plan saved!")


@router.callback_query(PlannerStates.waiting_for_tomorrow_plan, F.data == "cancel_night_plan")
async def cancel_tomorrow_planning(callback: CallbackQuery, state: FSMContext) -> None:
    """Cancel writing tomorrow's plan."""
    await state.clear()
    if callback.message:
        await callback.message.edit_text(
            "❌ <i>Nightly planning cancelled.</i>",
            reply_markup=None,
            parse_mode="HTML",
        )
    await callback.answer("Cancelled.")


# =============================================================================
# Core Feature 3: On-Demand To-Do List & Timed Alerts
# =============================================================================

@router.message(Command("todo"))
async def handle_todo_command(message: Message, db: Database) -> None:
    """
    Add a general To-Do item for today or view current to-do list:
      /todo [task text]
    """
    user_id = message.from_user.id
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)

    if len(parts) < 2 or not parts[1].strip():
        text_resp, keyboard = await build_planner_dashboard_message(user_id, db)
        await message.answer(
            f"{text_resp}\n\n💡 <i>To add a new task, send:</i> <code>/todo Buy milk</code> <i>or tap ➕ Quick Todo below.</i>",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        return

    task_text = parts[1].strip()
    if task_text in MAIN_MENU_BUTTONS:
        return

    today_str = get_today_str()

    task_id = await db.add_task(
        user_id=user_id,
        task_text=task_text,
        target_date=today_str,
        remind_at=None,
    )

    builder = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Mark Done", callback_data=f"done_task:{task_id}"),
                InlineKeyboardButton(text="🗑️ Delete", callback_data=f"del_task:{task_id}"),
            ]
        ]
    )

    await message.answer(
        f"✅ <b>Task Added for Today ({today_str}):</b>\n\n📌 {task_text}",
        reply_markup=builder,
        parse_mode="HTML",
    )


@router.message(StateFilter(None), F.text.func(is_natural_todo_add))
async def handle_natural_todo_add(message: Message, db: Database) -> None:
    """Adds a task when user types 'todo Buy groceries' or 'todo: Call client'."""
    text = (message.text or "").strip()
    low = text.lower()
    task_text = ""
    for prefix in ("todo: ", "todo ", "to-do: ", "to-do ", "to do: "):
        if low.startswith(prefix):
            task_text = text[len(prefix):].strip()
            break

    if not task_text or task_text in MAIN_MENU_BUTTONS:
        return

    user_id = message.from_user.id
    today_str = get_today_str()

    task_id = await db.add_task(
        user_id=user_id,
        task_text=task_text,
        target_date=today_str,
        remind_at=None,
    )

    builder = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Mark Done", callback_data=f"done_task:{task_id}"),
                InlineKeyboardButton(text="🗑️ Delete", callback_data=f"del_task:{task_id}"),
            ]
        ]
    )

    await message.answer(
        f"✅ <b>Task Added for Today ({today_str}):</b>\n\n📌 {task_text}",
        reply_markup=builder,
        parse_mode="HTML",
    )


@router.message(F.text == "➕ Quick Todo")
async def prompt_quick_todo_button(message: Message, state: FSMContext) -> None:
    """Prompt user to type a quick todo item via reply button."""
    await state.set_state(PlannerStates.waiting_for_quick_todo)
    await message.answer(
        "✍️ <b>Enter task text for today:</b>\n"
        "<i>(Send your task description, or /cancel to abort)</i>",
        parse_mode="HTML",
    )


@router.message(PlannerStates.waiting_for_quick_todo, F.text)
async def receive_quick_todo_text(
    message: Message, state: FSMContext, db: Database
) -> None:
    """Save quick todo entered via prompt."""
    if await handle_button_breakout(message, state, db):
        return

    task_text = (message.text or "").strip()
    if not task_text or task_text.startswith("/") or task_text in MAIN_MENU_BUTTONS:
        return

    user_id = message.from_user.id
    today_str = get_today_str()

    task_id = await db.add_task(
        user_id=user_id,
        task_text=task_text,
        target_date=today_str,
        remind_at=None,
    )
    await state.clear()

    builder = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Mark Done", callback_data=f"done_task:{task_id}"),
                InlineKeyboardButton(text="🗑️ Delete", callback_data=f"del_task:{task_id}"),
            ]
        ]
    )

    await message.answer(
        f"✅ <b>Task Added for Today ({today_str}):</b>\n\n📌 {task_text}",
        reply_markup=builder,
        parse_mode="HTML",
    )


@router.message(Command("remind", "alert"))
async def handle_remind_command(
    message: Message,
    db: Database,
    scheduler: AsyncIOScheduler,
) -> None:
    """
    Schedule a timed reminder at a specific time or date:
      - /remind 18:30 Call client
      - /alert 18:30 Call client
      - /remind 2026-11-20 14:00 Submit project
      - /alert 45m Check oven
    """
    user_id = message.from_user.id
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)

    if len(parts) < 2:
        await message.answer(
            "ℹ️ <b>Usage:</b>\n"
            "• Specific time today/tomorrow: <code>/remind 18:30 Call client</code>\n"
            "• Specific date & time: <code>/remind 2026-11-20 14:00 Submit project</code>",
            parse_mode="HTML",
        )
        return

    now = get_now()
    parsed = parse_remind_command(parts[1], now)
    if not parsed:
        await message.answer(
            "⚠️ <b>Invalid reminder format.</b> Examples:\n"
            "• <code>/remind 18:30 Call client</code>\n"
            "• <code>/remind 2026-11-20 14:00 Submit project</code>",
            parse_mode="HTML",
        )
        return

    remind_dt, task_text = parsed
    target_date = remind_dt.strftime("%Y-%m-%d")
    remind_at_str = format_dt(remind_dt)

    # 1. Save in database
    task_id = await db.add_task(
        user_id=user_id,
        task_text=task_text,
        target_date=target_date,
        remind_at=remind_at_str,
    )

    # 2. Schedule in APScheduler
    schedule_task_reminder(
        scheduler=scheduler,
        bot=message.bot,
        db=db,
        task_id=task_id,
        remind_dt=remind_dt,
        admin_user_id=user_id,
    )

    time_formatted = remind_dt.strftime("%Y-%m-%d %H:%M")
    await message.answer(
        f"⏰ <b>Reminder Scheduled!</b>\n\n"
        f"📌 Task: <b>{task_text}</b>\n"
        f"🔔 Alert at: <b>{time_formatted}</b> (Asia/Tashkent)",
        parse_mode="HTML",
    )


@router.message(Command("in"))
async def handle_in_command(
    message: Message,
    db: Database,
    scheduler: AsyncIOScheduler,
) -> None:
    """
    Schedule a reminder using relative duration:
      - /in 45m Check oven
      - /in 2h Gym workout
      - /in 1h30m Meeting
    """
    user_id = message.from_user.id
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)

    if len(parts) < 2:
        await message.answer(
            "ℹ️ <b>Usage:</b>\n"
            "• <code>/in 45m Check oven</code>\n"
            "• <code>/in 2h Gym workout</code>\n"
            "• <code>/in 1h30m Meeting</code>",
            parse_mode="HTML",
        )
        return

    parsed = parse_relative_time(parts[1])
    if not parsed:
        await message.answer(
            "⚠️ <b>Invalid format.</b> Examples:\n"
            "• <code>/in 45m Check oven</code>\n"
            "• <code>/in 2h Gym workout</code>",
            parse_mode="HTML",
        )
        return

    delta, task_text = parsed
    now = get_now()
    remind_dt = now + delta
    target_date = remind_dt.strftime("%Y-%m-%d")
    remind_at_str = format_dt(remind_dt)

    # 1. Save in database
    task_id = await db.add_task(
        user_id=user_id,
        task_text=task_text,
        target_date=target_date,
        remind_at=remind_at_str,
    )

    # 2. Schedule in APScheduler
    schedule_task_reminder(
        scheduler=scheduler,
        bot=message.bot,
        db=db,
        task_id=task_id,
        remind_dt=remind_dt,
        admin_user_id=user_id,
    )

    time_formatted = remind_dt.strftime("%H:%M:%S")
    await message.answer(
        f"⏳ <b>Timer Set!</b>\n\n"
        f"📌 Task: <b>{task_text}</b>\n"
        f"🔔 Alert at: <b>{time_formatted}</b> (in {parts[1].split()[0]})",
        parse_mode="HTML",
    )


# =============================================================================
# Interactive Snooze & Task Completion Callbacks
# =============================================================================

@router.callback_query(F.data.startswith("done_task:"))
async def handle_done_task(callback: CallbackQuery, db: Database) -> None:
    """Mark a task completed from alert, briefing, or dashboard."""
    parts = callback.data.split(":")
    if len(parts) != 2 or not parts[1].isdigit():
        await callback.answer("Invalid request.")
        return

    task_id = int(parts[1])
    user_id = callback.from_user.id

    task = await db.get_task_by_id(task_id)
    if not task:
        await callback.answer("Task not found or already removed.", show_alert=True)
        return

    await db.mark_task_completed(task_id, user_id)
    await callback.answer("Task marked as Done! 🎉")

    done_msg = f"✅ <b>Completed:</b> <s>{task['task_text']}</s>"
    if callback.message:
        # If in an alert or briefing, update text or reply markup
        try:
            await callback.message.edit_text(done_msg, parse_mode="HTML")
        except Exception:
            # If cannot edit original (e.g. from dashboard keyboard), refresh dashboard
            pass


@router.callback_query(F.data.startswith("snooze_task:"))
async def handle_snooze_task(
    callback: CallbackQuery,
    db: Database,
    scheduler: AsyncIOScheduler,
) -> None:
    """
    Interactive Snooze:
      - snooze_task:<id>:15 -> +15 minutes
      - snooze_task:<id>:60 -> +1 hour
    """
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await callback.answer("Invalid snooze data.")
        return

    task_id = int(parts[1])
    minutes = int(parts[2])
    user_id = callback.from_user.id

    task = await db.get_task_by_id(task_id)
    if not task:
        await callback.answer("Task not found.", show_alert=True)
        return

    now = get_now()
    new_remind_dt = now + timedelta(minutes=minutes)
    new_remind_str = format_dt(new_remind_dt)

    # 1. Update database
    await db.reschedule_reminder(task_id, new_remind_str)

    # 2. Reschedule job in APScheduler
    schedule_task_reminder(
        scheduler=scheduler,
        bot=callback.bot,
        db=db,
        task_id=task_id,
        remind_dt=new_remind_dt,
        admin_user_id=user_id,
    )

    time_fmt = new_remind_dt.strftime("%H:%M")
    snooze_text = (
        f"💤 <b>Snoozed for {minutes} min!</b>\n\n"
        f"📌 Task: <b>{task['task_text']}</b>\n"
        f"🔔 Next alert at: <b>{time_fmt}</b>"
    )

    if callback.message:
        await callback.message.edit_text(
            snooze_text,
            reply_markup=get_reminder_alert_keyboard(task_id),
            parse_mode="HTML",
        )
    await callback.answer(f"Snoozed +{minutes}m")


# =============================================================================
# Interactive Planner Menu (/plan and 📅 My Planner)
# =============================================================================

@router.message(Command("plan", "tasks", "todos", "todo_list"))
@router.message(F.text == "📅 My Planner")
@router.message(
    StateFilter(None),
    F.text.func(lambda t: t and t.strip().lower() in PLANNER_QUERIES)
)
async def show_planner_dashboard(message: Message, db: Database) -> None:
    """Render the interactive daily planner dashboard."""
    user_id = message.from_user.id
    text, keyboard = await build_planner_dashboard_message(user_id, db)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "refresh_planner")
async def refresh_planner_callback(callback: CallbackQuery, db: Database) -> None:
    """Refresh the planner dashboard in-place."""
    user_id = callback.from_user.id
    text, keyboard = await build_planner_dashboard_message(user_id, db)
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        except Exception:
            pass
    await callback.answer("Planner refreshed.")


@router.callback_query(F.data == "action:add_today")
async def callback_add_today(callback: CallbackQuery, state: FSMContext) -> None:
    """Start adding task for today."""
    await state.set_state(PlannerStates.waiting_for_single_today_task)
    if callback.message:
        await callback.message.answer(
            "✍️ Send the task description to add to <b>Today's Plan</b>:",
            parse_mode="HTML",
        )
    await callback.answer()


@router.message(PlannerStates.waiting_for_single_today_task, F.text)
async def receive_single_today_task(
    message: Message, state: FSMContext, db: Database
) -> None:
    """Receive task for today."""
    if await handle_button_breakout(message, state, db):
        return

    task_text = (message.text or "").strip()
    if not task_text or task_text.startswith("/") or task_text in MAIN_MENU_BUTTONS:
        return

    user_id = message.from_user.id
    today_str = get_today_str()
    await db.add_task(user_id, task_text, today_str, None)
    await state.clear()

    await message.answer(
        f"✅ Added to Today: <b>{task_text}</b>",
        parse_mode="HTML",
    )
    # Refresh dashboard
    text, keyboard = await build_planner_dashboard_message(user_id, db)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "action:add_tomorrow")
async def callback_add_tomorrow(callback: CallbackQuery, state: FSMContext) -> None:
    """Start adding task for tomorrow."""
    await state.set_state(PlannerStates.waiting_for_single_tomorrow_task)
    if callback.message:
        await callback.message.answer(
            "✍️ Send the task description to add to <b>Tomorrow's Plan</b>:",
            parse_mode="HTML",
        )
    await callback.answer()


@router.message(PlannerStates.waiting_for_single_tomorrow_task, F.text)
async def receive_single_tomorrow_task(
    message: Message, state: FSMContext, db: Database
) -> None:
    """Receive task for tomorrow."""
    if await handle_button_breakout(message, state, db):
        return

    task_text = (message.text or "").strip()
    if not task_text or task_text.startswith("/") or task_text in MAIN_MENU_BUTTONS:
        return

    user_id = message.from_user.id
    tomorrow_str = get_tomorrow_str()
    await db.add_task(user_id, task_text, tomorrow_str, None)
    await state.clear()

    await message.answer(
        f"✅ Added to Tomorrow: <b>{task_text}</b>",
        parse_mode="HTML",
    )
    # Refresh dashboard
    text, keyboard = await build_planner_dashboard_message(user_id, db)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "action:delete_menu")
async def callback_delete_menu(callback: CallbackQuery, db: Database) -> None:
    """Display task deletion selection menu."""
    user_id = callback.from_user.id
    today_str = get_today_str()
    tasks = await db.get_tasks_for_date(user_id, today_str)

    if not tasks:
        await callback.answer("No tasks today to delete.", show_alert=True)
        return

    keyboard = get_task_delete_keyboard(tasks)
    if callback.message:
        await callback.message.edit_text(
            "🗑️ <b>Select a task to delete:</b>",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data.startswith("del_task:"))
async def handle_delete_task(
    callback: CallbackQuery,
    db: Database,
    scheduler: AsyncIOScheduler,
) -> None:
    """Delete a task and cancel any scheduled reminder for it."""
    parts = callback.data.split(":")
    if len(parts) != 2 or not parts[1].isdigit():
        await callback.answer("Invalid request.")
        return

    task_id = int(parts[1])
    user_id = callback.from_user.id

    cancel_task_reminder(scheduler, task_id)
    deleted = await db.delete_task(task_id, user_id)

    if deleted:
        await callback.answer("Task deleted.")
    else:
        await callback.answer("Could not find task.", show_alert=True)

    # Return to planner dashboard
    text, keyboard = await build_planner_dashboard_message(user_id, db)
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        except Exception:
            pass


@router.callback_query(F.data == "action:alert_menu")
async def callback_alert_menu(callback: CallbackQuery, db: Database) -> None:
    """Display task alert setting menu for today and tomorrow's active tasks."""
    user_id = callback.from_user.id
    today_str = get_today_str()
    tomorrow_str = get_tomorrow_str()

    today_tasks = await db.get_tasks_for_date(user_id, today_str)
    tomorrow_tasks = await db.get_tasks_for_date(user_id, tomorrow_str)

    # Filter uncompleted tasks
    pending = [t for t in (today_tasks + tomorrow_tasks) if t.get("is_completed", 0) == 0]

    if not pending:
        await callback.answer("No active tasks found to set alerts for.", show_alert=True)
        return

    keyboard = get_task_alert_selection_keyboard(pending)
    if callback.message:
        await callback.message.edit_text(
            "⏰ <b>Select a task to attach an alert/reminder to:</b>",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data.startswith("set_alert_task:"))
async def callback_set_alert_task(
    callback: CallbackQuery, state: FSMContext, db: Database
) -> None:
    """Prompt user to enter the alert time for the selected task."""
    parts = callback.data.split(":")
    if len(parts) != 2 or not parts[1].isdigit():
        await callback.answer("Invalid request.")
        return

    task_id = int(parts[1])
    task = await db.get_task_by_id(task_id)
    if not task:
        await callback.answer("Task not found.", show_alert=True)
        return

    await state.set_state(PlannerStates.waiting_for_task_alert_time)
    await state.update_data(alert_task_id=task_id, alert_task_text=task["task_text"])

    prompt = (
        f"⏰ <b>Set Alert for:</b> \"<b>{task['task_text']}</b>\"\n\n"
        "Send the alert time in any of these formats:\n"
        "• Specific time: <code>18:30</code> or <code>14:00</code>\n"
        "• Relative time: <code>30m</code> or <code>45m</code> or <code>2h</code>\n"
        "• Specific date: <code>2026-11-15 14:00</code>\n\n"
        "<i>(Type /cancel to abort)</i>"
    )

    if callback.message:
        await callback.message.edit_text(prompt, reply_markup=None, parse_mode="HTML")
    await callback.answer()


@router.message(PlannerStates.waiting_for_task_alert_time, F.text)
async def receive_task_alert_time(
    message: Message,
    state: FSMContext,
    db: Database,
    scheduler: AsyncIOScheduler,
) -> None:
    """Parse time and attach alert to the selected task."""
    if await handle_button_breakout(message, state, db):
        return

    time_text = (message.text or "").strip()
    if not time_text or time_text.startswith("/") or time_text in MAIN_MENU_BUTTONS:
        return

    data = await state.get_data()
    task_id = data.get("alert_task_id")
    task_text = data.get("alert_task_text", "Task")
    user_id = message.from_user.id
    now = get_now()

    # Parse time (dummy task text appended to reuse parse_remind_command)
    parsed = parse_remind_command(f"{time_text} {task_text}", now)
    if not parsed:
        await message.answer(
            "⚠️ <b>Invalid time format.</b> Please try again:\n"
            "• Examples: <code>18:30</code>, <code>45m</code>, <code>2h</code>, or <code>2026-11-15 14:00</code>\n"
            "• Or /cancel to exit.",
            parse_mode="HTML",
        )
        return

    remind_dt, _ = parsed
    remind_at_str = format_dt(remind_dt)

    # 1. Update database
    await db.reschedule_reminder(task_id, remind_at_str)

    # 2. Schedule in APScheduler
    schedule_task_reminder(
        scheduler=scheduler,
        bot=message.bot,
        db=db,
        task_id=task_id,
        remind_dt=remind_dt,
        admin_user_id=user_id,
    )
    await state.clear()

    time_formatted = remind_dt.strftime("%Y-%m-%d %H:%M")
    await message.answer(
        f"✅ <b>Alert Set!</b>\n\n"
        f"📌 Task: <b>{task_text}</b>\n"
        f"🔔 Alert at: <b>{time_formatted}</b> (Asia/Tashkent)",
        parse_mode="HTML",
    )

    # Show updated dashboard
    text, keyboard = await build_planner_dashboard_message(user_id, db)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
