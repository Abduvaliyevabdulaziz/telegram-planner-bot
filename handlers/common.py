"""
Common and General Command Handlers (/start, /help, /cancel, Today's Summary).
"""
from __future__ import annotations

import logging
from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from config import (
    settings,
    get_now,
    get_today_str,
    format_currency,
)
from database import Database
from keyboards import get_main_reply_keyboard

logger = logging.getLogger(__name__)

router = Router(name="common_router")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    """Handler for /start command. Greets the admin and presents instructions."""
    await state.clear()
    welcome_text = (
        "👋 <b>Welcome to your Personal Expense Tracker & Smart Daily Planner!</b>\n\n"
        "Configured for: <b>Asia/Tashkent (UTC+5)</b>\n"
        f"Daily Spending Limit: <b>{format_currency(settings.DAILY_BUDGET_LIMIT)}</b>\n\n"
        "<b>⚡ Quick Features:</b>\n"
        "• <b>Quick Expense:</b> Just send <code>45000 lunch</code> or <code>12k taxi</code>\n"
        "• <b>Quick To-Do:</b> Use <code>/todo Buy groceries</code>\n"
        "• <b>Timed Reminder:</b> <code>/remind 18:30 Call client</code> or <code>/in 45m Check oven</code>\n"
        "• <b>Evening Loop:</b> Prompted every evening at <b>21:00</b> for tomorrow's plan\n"
        "• <b>Morning Briefing:</b> Delivered every morning at <b>09:00</b> with tasks & summary\n\n"
        "Type /help anytime to see the full command list."
    )
    await message.answer(
        welcome_text,
        reply_markup=get_main_reply_keyboard(),
        parse_mode="HTML",
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Handler for /help command. Comprehensive user guide."""
    help_text = (
        "📖 <b>Bot Commands & Usage Guide:</b>\n\n"
        "<b>💸 Expense Tracker:</b>\n"
        "• <code>45000 Fast food</code> — Log 45,000 UZS for Fast food\n"
        "• <code>12k taxi</code> — 'k' shorthand (12k = 12,000 UZS)\n"
        "• <code>25000 coffee</code> — Logs 25,000 UZS for coffee\n"
        "• <code>70k</code> — Quick entry with default category\n"
        "• /expenses — View interactive filter menu (Today, Week, Month, 2 Months)\n"
        "• /spent YYYY-MM-DD — View breakdown for a specific date\n"
        "• /spent YYYY-MM-DD YYYY-MM-DD — Breakdown for a date range\n"
        "• /export — Download full expenses history as a .CSV file\n\n"
        "<b>📅 Daily Planner & Reminders:</b>\n"
        "• /plan — View Today & Tomorrow's interactive task dashboard\n"
        "• /plan_tomorrow — Start writing your plan for tomorrow\n"
        "• /todo [text] — Add a quick to-do for today\n"
        "• /remind [HH:MM] [text] — Timed alert for today/tomorrow (e.g. <code>/remind 18:30 Call client</code>)\n"
        "• /remind [YYYY-MM-DD HH:MM] [text] — Specific date alert\n"
        "• /in [duration] [text] — Relative timer (e.g. <code>/in 45m Check oven</code>, <code>/in 2h Gym</code>)\n\n"
        "<b>🔄 Automated Daily Loop:</b>\n"
        "• <b>21:00 Tashkent:</b> Prompts you to plan tomorrow's tasks\n"
        "• <b>09:00 Tashkent:</b> Sends your morning briefing with tasks & yesterday's spending\n\n"
        "<b>Other:</b>\n"
        "• /cancel — Cancel current action / exit state"
    )
    await message.answer(
        help_text,
        reply_markup=get_main_reply_keyboard(),
        parse_mode="HTML",
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Cancel any active FSM state."""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer(
            "ℹ️ No active action to cancel.",
            reply_markup=get_main_reply_keyboard(),
        )
        return

    await state.clear()
    await message.answer(
        "❌ Action cancelled. Returned to main menu.",
        reply_markup=get_main_reply_keyboard(),
    )


@router.message(F.text == "📊 Today's Summary")
async def handle_todays_summary(message: Message, db: Database) -> None:
    """Show an instant high-level overview of today's expenses and tasks."""
    user_id = message.from_user.id
    today_str = get_today_str()
    now = get_now()

    # Expenses summary
    today_spent = await db.get_expenses_sum_for_date(user_id, today_str)
    limit = settings.DAILY_BUDGET_LIMIT

    # Tasks summary
    today_tasks = await db.get_tasks_for_date(user_id, today_str)
    completed_count = sum(1 for t in today_tasks if t.get("is_completed", 0) == 1)
    pending_count = len(today_tasks) - completed_count

    # Upcoming reminder today
    reminders = await db.get_scheduled_reminders_for_date(user_id, today_str)
    future_reminders = [
        r for r in reminders
        if r.get("is_completed", 0) == 0 and r.get("remind_at", "") >= now.strftime("%Y-%m-%d %H:%M:%S")
    ]

    # Format text
    lines = [
        f"📊 <b>Today's Summary — {today_str}</b>",
        f"🕒 Current Time: <b>{now.strftime('%H:%M:%S')}</b> (Asia/Tashkent)\n",
        "<b>💸 Spending:</b>",
        f"• Spent Today: <b>{format_currency(today_spent)}</b>",
    ]

    if today_spent <= limit:
        remaining = limit - today_spent
        lines.append(f"• Remaining Budget: <b>{format_currency(remaining)}</b> (out of {format_currency(limit)}) ✅")
    else:
        over = today_spent - limit
        lines.append(
            f"• ⚠️ <b>Budget Exceeded:</b> +{format_currency(over)} over {format_currency(limit)} limit!"
        )

    lines.append("\n<b>📋 Tasks for Today:</b>")
    lines.append(f"• Total Tasks: {len(today_tasks)} (✅ Completed: {completed_count} | ⬜ Pending: {pending_count})")

    if future_reminders:
        next_rem = future_reminders[0]
        time_part = next_rem['remind_at'][11:16]
        lines.append(f"• ⏰ Next Alert: <b>{time_part}</b> — {next_rem['task_text']}")
    else:
        lines.append("• ⏰ Next Alert: <i>None remaining for today</i>")

    await message.answer(
        "\n".join(lines),
        reply_markup=get_main_reply_keyboard(),
        parse_mode="HTML",
    )
