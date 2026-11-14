"""
Inline and Reply Keyboard builders for the Telegram Bot.
"""
from __future__ import annotations

from typing import Any
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder


def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Persistent main menu reply keyboard."""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="💸 Expenses"),
        KeyboardButton(text="📅 My Planner"),
    )
    builder.row(
        KeyboardButton(text="➕ Quick Todo"),
        KeyboardButton(text="📊 Today's Summary"),
    )
    return builder.as_markup(resize_keyboard=True)


def get_expense_undo_keyboard(expense_id: int) -> InlineKeyboardMarkup:
    """Inline button to undo/delete a newly logged expense."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="❌ Undo / Delete",
        callback_data=f"del_exp:{expense_id}",
    )
    return builder.as_markup()


def get_expenses_menu_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard for expense report filtering and CSV export."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📅 Today", callback_data="filter_exp:today"),
        InlineKeyboardButton(text="🗓️ This Week", callback_data="filter_exp:week"),
    )
    builder.row(
        InlineKeyboardButton(text="📆 This Month", callback_data="filter_exp:month"),
        InlineKeyboardButton(text="📊 Last 2 Months", callback_data="filter_exp:2months"),
    )
    builder.row(
        InlineKeyboardButton(text="📥 Export Full CSV", callback_data="export_expenses_csv"),
    )
    return builder.as_markup()


def get_night_plan_prompt_keyboard() -> InlineKeyboardMarkup:
    """Inline button for the 21:00 evening planning prompt."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="📝 Write Tomorrow's Plan",
        callback_data="start_night_plan",
    )
    return builder.as_markup()


def get_night_plan_active_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard shown while writing tomorrow's plan."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Save Plan", callback_data="finish_night_plan"),
        InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_night_plan"),
    )
    return builder.as_markup()


def get_reminder_alert_keyboard(task_id: int) -> InlineKeyboardMarkup:
    """Interactive snooze and complete keyboard for timed alerts."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Done", callback_data=f"done_task:{task_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="💤 +15 min", callback_data=f"snooze_task:{task_id}:15"),
        InlineKeyboardButton(text="💤 +1 hour", callback_data=f"snooze_task:{task_id}:60"),
    )
    return builder.as_markup()


def get_morning_briefing_keyboard(today_tasks: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    """Interactive buttons for uncompleted tasks in the morning briefing."""
    builder = InlineKeyboardBuilder()
    for task in today_tasks:
        if task.get("is_completed", 0) == 0:
            tid = task["id"]
            short_text = task["task_text"][:25]
            builder.row(
                InlineKeyboardButton(
                    text=f"✅ Mark Done: {short_text}",
                    callback_data=f"done_task:{tid}",
                )
            )
    builder.row(
        InlineKeyboardButton(text="📅 Open Full Planner", callback_data="refresh_planner"),
    )
    return builder.as_markup()


def get_planner_dashboard_keyboard(
    today_tasks: list[dict[str, Any]], tomorrow_tasks: list[dict[str, Any]]
) -> InlineKeyboardMarkup:
    """Interactive keyboard for the main planner menu."""
    builder = InlineKeyboardBuilder()

    # Show toggle buttons for up to 6 pending tasks today
    pending_today = [t for t in today_tasks if t.get("is_completed", 0) == 0]
    for task in pending_today[:6]:
        tid = task["id"]
        short_text = task["task_text"][:22]
        builder.row(
            InlineKeyboardButton(
                text=f"✅ Done: {short_text}",
                callback_data=f"done_task:{tid}",
            )
        )

    # Management buttons
    builder.row(
        InlineKeyboardButton(text="➕ Add Today Task", callback_data="action:add_today"),
        InlineKeyboardButton(text="➕ Add Tomorrow Task", callback_data="action:add_tomorrow"),
    )
    builder.row(
        InlineKeyboardButton(text="🗑️ Delete a Task", callback_data="action:delete_menu"),
        InlineKeyboardButton(text="🔄 Refresh", callback_data="refresh_planner"),
    )
    return builder.as_markup()


def get_task_delete_keyboard(tasks: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    """Inline keyboard listing tasks available for deletion."""
    builder = InlineKeyboardBuilder()
    for task in tasks[:10]:
        tid = task["id"]
        status = "✅" if task.get("is_completed", 0) == 1 else "⬜"
        short_text = task["task_text"][:25]
        builder.row(
            InlineKeyboardButton(
                text=f"🗑️ {status} {short_text}",
                callback_data=f"del_task:{tid}",
            )
        )
    builder.row(
        InlineKeyboardButton(text="🔙 Back to Planner", callback_data="refresh_planner")
    )
    return builder.as_markup()
