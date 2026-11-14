"""
Expenses Tracker Router.
Handles natural text expense logging, 70k limit alert, undo, filtering, reports, and CSV export.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime, timedelta
from typing import Any
from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, BufferedInputFile

from config import (
    settings,
    get_now,
    get_today_str,
    format_currency,
    format_dt,
)
from database import Database
from keyboards import (
    get_expense_undo_keyboard,
    get_expenses_menu_keyboard,
    get_main_reply_keyboard,
)

logger = logging.getLogger(__name__)

router = Router(name="expenses_router")


# =============================================================================
# Helper & Parser Functions
# =============================================================================

def parse_expense_text(text: str) -> tuple[int, str] | None:
    """
    Parses natural text starting with numbers.
    Supports 'k' / 'к' shorthand for thousands:
      - '45000 Fast food' -> (45000, 'Fast food')
      - '12k taxi' -> (12000, 'taxi')
      - '25000 coffee' -> (25000, 'coffee')
      - '12.5k lunch' -> (12500, 'lunch')
      - '50 000 groceries' -> (50000, 'groceries')
      - '70k' -> (70000, 'General')
    Returns (amount, category) or None if not matching.
    """
    cleaned = text.strip()
    if not cleaned or cleaned.startswith("/"):
        return None

    # Matches starting with a number (with optional spaces/dots) and optional 'k'
    pattern = r"^(\d+(?:[\s_]\d+)*(?:\.\d+)?)\s*([kKкК]?)\s*(.*)$"
    match = re.match(pattern, cleaned)
    if not match:
        return None

    num_str, k_suffix, category_part = match.groups()
    num_clean = num_str.replace(" ", "").replace("_", "")

    try:
        val = float(num_clean)
        if k_suffix.lower() in ("k", "к"):
            val *= 1000
        amount = int(round(val))
        if amount <= 0:
            return None

        category = category_part.strip()
        if not category:
            category = "General"

        return amount, category
    except (ValueError, OverflowError):
        return None


def format_report_message(
    title: str, stats: dict[str, Any], date_subtitle: str = ""
) -> str:
    """Formats the expense statistics according to requirements."""
    total = stats.get("total", 0)
    count = stats.get("count", 0)
    categories = stats.get("categories", [])
    recent = stats.get("recent_transactions", [])

    lines = [f"📊 <b>{title}</b>"]
    if date_subtitle:
        lines.append(f"📅 <i>{date_subtitle}</i>")
    lines.append("")

    if count == 0 or total == 0:
        lines.append("<i>No expenses recorded for this period.</i>")
        return "\n".join(lines)

    lines.append(f"💰 <b>Grand Total:</b> <b>{format_currency(total)}</b> ({count} entries)\n")

    # Grouped Breakdown by Category
    lines.append("🏷️ <b>Grouped Breakdown by Category:</b>")
    for cat in categories:
        c_name = cat["category"]
        c_amt = format_currency(cat["amount"])
        c_pct = cat["percentage"]
        lines.append(f"• <b>{c_name}:</b> {c_amt} ({c_pct}%)")

    # Last 10 individual transactions
    lines.append("\n📝 <b>Last Individual Transactions:</b>")
    for tx in recent[:10]:
        time_part = tx["created_at"][5:16]  # MM-DD HH:MM
        tx_amt = format_currency(tx["amount"])
        tx_cat = tx["category"]
        lines.append(f"• <code>{time_part}</code> — <b>{tx_amt}</b> ({tx_cat})")

    return "\n".join(lines)


# =============================================================================
# Core Feature 1: Natural Text Logging & Budget Alert
# =============================================================================

@router.message(StateFilter(None), F.text)
async def handle_natural_expense_logging(message: Message, db: Database) -> None:
    """
    Catches natural text messages starting with a number when not in an FSM state.
    Logs expense, checks today's 70,000 UZS limit, and replies with confirmation + undo button.
    """
    text = message.text or ""
    parsed = parse_expense_text(text)
    if not parsed:
        # Not an expense message, let other handlers process or ignore
        return

    amount, category = parsed
    user_id = message.from_user.id
    now = get_now()
    created_at = format_dt(now)
    today_str = get_today_str()

    # 1. Save expense to database
    expense_id = await db.add_expense(
        user_id=user_id,
        amount=amount,
        category=category,
        created_at=created_at,
    )

    # 2. Calculate today's total spending so far
    today_total = await db.get_expenses_sum_for_date(user_id, today_str)
    budget_limit = settings.DAILY_BUDGET_LIMIT

    # 3. Format confirmation reply
    lines = [
        "✅ <b>Expense Logged!</b>",
        f"💸 Amount: <b>{format_currency(amount)}</b>",
        f"🏷️ Category: <b>{category}</b>",
        f"📊 Today's Total: <b>{format_currency(today_total)}</b>",
    ]

    if today_total <= budget_limit:
        remaining = budget_limit - today_total
        lines.append(
            f"🎯 Remaining Daily Budget: <b>{format_currency(remaining)}</b> (out of {format_currency(budget_limit)})"
        )
    else:
        # 70,000 UZS Daily Budget Cap Alert
        over_amount = today_total - budget_limit
        lines.append(
            f"🎯 Remaining Daily Budget: <b>0 UZS</b> (out of {format_currency(budget_limit)})\n\n"
            f"⚠️ <b>BUDGET ALERT: You have exceeded your {format_currency(budget_limit)} daily limit!</b>\n"
            f"Today's total: <b>{format_currency(today_total)}</b> "
            f"(+<b>{format_currency(over_amount)}</b> over limit)."
        )

    reply_text = "\n".join(lines)
    keyboard = get_expense_undo_keyboard(expense_id)

    await message.answer(reply_text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("del_exp:"))
async def handle_undo_expense(callback: CallbackQuery, db: Database) -> None:
    """Handles the ❌ Undo / Delete inline button for an expense entry."""
    parts = callback.data.split(":")
    if len(parts) != 2 or not parts[1].isdigit():
        await callback.answer("Invalid request.", show_alert=True)
        return

    expense_id = int(parts[1])
    user_id = callback.from_user.id

    # Retrieve info before deletion
    exp = await db.get_expense_by_id(expense_id)
    if not exp or exp.get("user_id") != user_id:
        await callback.answer("⚠️ Expense already deleted or not found.", show_alert=True)
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)
        return

    deleted = await db.delete_expense(expense_id, user_id)
    if deleted:
        today_str = get_today_str()
        new_today_total = await db.get_expenses_sum_for_date(user_id, today_str)
        rem = max(0, settings.DAILY_BUDGET_LIMIT - new_today_total)

        confirm_msg = (
            f"🗑️ <b>Expense Undone & Deleted!</b>\n\n"
            f"Removed: <b>{format_currency(exp['amount'])}</b> ({exp['category']})\n"
            f"Today's New Total: <b>{format_currency(new_today_total)}</b>\n"
            f"Remaining Daily Budget: <b>{format_currency(rem)}</b>"
        )
        if callback.message:
            await callback.message.edit_text(confirm_msg, parse_mode="HTML")
        await callback.answer("Expense deleted.")
    else:
        await callback.answer("Could not delete expense.", show_alert=True)


# =============================================================================
# Expense Reports & Filtering
# =============================================================================

@router.message(Command("expenses"))
@router.message(F.text == "💸 Expenses")
async def show_expenses_menu(message: Message) -> None:
    """Presents the expense reports menu with filter options."""
    text = (
        "💸 <b>Expenses Dashboard & Reports</b>\n\n"
        "Choose a time period to view the breakdown and transactions, "
        "or export all data to CSV:"
    )
    await message.answer(
        text,
        reply_markup=get_expenses_menu_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("filter_exp:"))
async def handle_expense_filter_callback(callback: CallbackQuery, db: Database) -> None:
    """Handles filter button clicks: today, week, month, 2months."""
    period = callback.data.split(":")[1]
    user_id = callback.from_user.id
    now = get_now()

    if period == "today":
        today_str = get_today_str()
        start_dt = f"{today_str} 00:00:00"
        end_dt = f"{today_str} 23:59:59"
        title = "Today's Expense Report"
        subtitle = f"{today_str}"
    elif period == "week":
        week_ago_str = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        today_str = get_today_str()
        start_dt = f"{week_ago_str} 00:00:00"
        end_dt = f"{today_str} 23:59:59"
        title = "This Week's Expense Report (Last 7 Days)"
        subtitle = f"{week_ago_str} to {today_str}"
    elif period == "month":
        month_start_str = now.strftime("%Y-%m-01")
        today_str = get_today_str()
        start_dt = f"{month_start_str} 00:00:00"
        end_dt = f"{today_str} 23:59:59"
        title = f"This Month's Report ({now.strftime('%B %Y')})"
        subtitle = f"{month_start_str} to {today_str}"
    elif period == "2months":
        sixty_days_str = (now - timedelta(days=60)).strftime("%Y-%m-%d")
        today_str = get_today_str()
        start_dt = f"{sixty_days_str} 00:00:00"
        end_dt = f"{today_str} 23:59:59"
        title = "Last 2 Months Report (60 Days)"
        subtitle = f"{sixty_days_str} to {today_str}"
    else:
        await callback.answer("Unknown period.")
        return

    stats = await db.get_expense_stats_by_period(user_id, start_dt, end_dt)
    report_text = format_report_message(title, stats, subtitle)

    # Edit message to show report and keep keyboard for further filtering
    if callback.message:
        await callback.message.edit_text(
            report_text,
            reply_markup=get_expenses_menu_keyboard(),
            parse_mode="HTML",
        )
    await callback.answer()


@router.message(Command("spent"))
async def handle_spent_command(message: Message, db: Database) -> None:
    """
    Custom date or date-range command:
      - /spent YYYY-MM-DD
      - /spent YYYY-MM-DD YYYY-MM-DD
    """
    user_id = message.from_user.id
    args = (message.text or "").strip().split()[1:]

    if not args:
        await message.answer(
            "ℹ️ <b>Usage:</b>\n"
            "• Single date: <code>/spent 2026-11-14</code>\n"
            "• Date range: <code>/spent 2026-11-01 2026-11-14</code>",
            parse_mode="HTML",
        )
        return

    # Case 1: Single Date
    if len(args) == 1:
        date_str = args[0]
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            await message.answer(
                "⚠️ Invalid date format. Please use <code>YYYY-MM-DD</code> (e.g. <code>/spent 2026-11-14</code>).",
                parse_mode="HTML",
            )
            return

        start_dt = f"{date_str} 00:00:00"
        end_dt = f"{date_str} 23:59:59"
        title = f"Expense Report for {date_str}"
        subtitle = date_str

    # Case 2: Date Range
    elif len(args) == 2:
        d1, d2 = args[0], args[1]
        try:
            dt1 = datetime.strptime(d1, "%Y-%m-%d")
            dt2 = datetime.strptime(d2, "%Y-%m-%d")
            if dt1 > dt2:
                d1, d2 = d2, d1
        except ValueError:
            await message.answer(
                "⚠️ Invalid date format. Please use <code>YYYY-MM-DD YYYY-MM-DD</code>.",
                parse_mode="HTML",
            )
            return

        start_dt = f"{d1} 00:00:00"
        end_dt = f"{d2} 23:59:59"
        title = f"Expense Report ({d1} to {d2})"
        subtitle = f"{d1} to {d2}"
    else:
        await message.answer(
            "⚠️ Too many arguments. Use <code>/spent YYYY-MM-DD</code> or <code>/spent YYYY-MM-DD YYYY-MM-DD</code>.",
            parse_mode="HTML",
        )
        return

    stats = await db.get_expense_stats_by_period(user_id, start_dt, end_dt)
    report_text = format_report_message(title, stats, subtitle)
    await message.answer(report_text, parse_mode="HTML")


# =============================================================================
# CSV Export
# =============================================================================

@router.message(Command("export"))
@router.callback_query(F.data == "export_expenses_csv")
async def handle_export_csv(event: Message | CallbackQuery, db: Database) -> None:
    """Generate and send a CSV file of all logged expenses."""
    user_id = event.from_user.id
    now = get_now()

    expenses = await db.get_all_expenses(user_id)
    if not expenses:
        msg_text = "ℹ️ No expenses found in the database to export."
        if isinstance(event, CallbackQuery):
            await event.answer("No expenses found.", show_alert=True)
        else:
            await event.answer(msg_text)
        return

    # Build CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Timestamp (Asia/Tashkent)", "Amount (UZS)", "Category"])

    total_amount = 0
    for exp in expenses:
        writer.writerow([
            exp["id"],
            exp["created_at"],
            exp["amount"],
            exp["category"],
        ])
        total_amount += exp["amount"]

    # Summary row at bottom
    writer.writerow([])
    writer.writerow(["TOTAL", "", total_amount, ""])

    csv_bytes = output.getvalue().encode("utf-8-sig")
    filename = f"expenses_export_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    input_file = BufferedInputFile(csv_bytes, filename=filename)

    caption = (
        f"📁 <b>Expenses Export</b>\n"
        f"• Total Entries: {len(expenses)}\n"
        f"• Grand Total: <b>{format_currency(total_amount)}</b>\n"
        f"• Generated: {format_dt(now)} (Asia/Tashkent)"
    )

    if isinstance(event, CallbackQuery):
        await event.answer("Generating CSV...")
        if event.message:
            await event.message.answer_document(
                document=input_file,
                caption=caption,
                parse_mode="HTML",
            )
    else:
        await event.answer_document(
            document=input_file,
            caption=caption,
            parse_mode="HTML",
        )
