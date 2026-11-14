# 📱 Personal Expense Tracker & Smart Daily Planner Telegram Bot

A production-ready, asynchronous personal assistant bot built with **Python 3.11+**, **aiogram 3.x**, **aiosqlite**, and **APScheduler**. Strictly configured to the **Asia/Tashkent (UTC+5)** timezone.

---

## 🌟 Key Features

### 1. Smart Expense Tracker & 70,000 UZS Daily Limit Alert
- **Quick Natural Text Logging:** Send messages starting with a number (e.g. `45000 Fast food`, `12k taxi`, `25000 coffee`).
  - Supports `k` / `к` shorthand for thousands (`12k` = `12,000 UZS`, `12.5k` = `12,500 UZS`).
  - Automatically saves entry with amount, category, and timestamp.
  - Returns a detailed receipt showing amount, category, today's total, and remaining daily budget.
  - **Inline Undo / Delete:** Instant `❌ Undo / Delete` button to remove mistakes.
- **70,000 UZS Daily Budget Cap Alert:**
  - If a logged expense causes today's total spending to exceed **70,000 UZS**, immediately appends an alert:
    `⚠️ BUDGET ALERT: You have exceeded your 70,000 UZS daily limit! Today's total: [Total] UZS (+[Over_Amount] UZS over limit).`
- **Interactive Reports & Filtering:**
  - Inline buttons for **Today**, **This Week** (last 7 days), **This Month**, and **Last 2 Months** (last 60 days).
  - Custom date or date-range command:
    - `/spent YYYY-MM-DD` (for a specific day)
    - `/spent YYYY-MM-DD YYYY-MM-DD` (between two dates)
  - Displays **Grand Total**, **Grouped Breakdown by Category with Percentages** (e.g. `Fast food: 45,000 UZS (64%)`), and the last 10 individual transactions.
- **CSV Export:**
  - Command `/export` or inline button `📥 Export Full CSV` sends a generated `.csv` file of all expenses.

---

### 2. Automated Daily Loop (Asia/Tashkent Timezone)
- **🌙 21:00 (9:00 PM) Nightly Planning Prompt:**
  - APScheduler sends: `🌙 Good evening! Time to plan tomorrow. What are your main tasks and goals for tomorrow?`
  - Inline button `📝 Write Tomorrow's Plan` or command `/plan_tomorrow`.
  - Enter tasks via a multi-line message (each line is a task) or send multiple messages.
  - Tap `✅ Save Plan` to commit tasks for tomorrow's date (`target_date = YYYY-MM-DD`).
- **🌅 09:00 (9:00 AM) Morning Briefing:**
  - **Today's Plan:** Exact list of tasks written the night before with interactive `✅ Done` buttons.
  - **Scheduled Reminders:** All timed alerts scheduled for today.
  - **Yesterday's Spending Summary:** Total UZS spent yesterday and whether you stayed under or exceeded the 70,000 UZS limit.

---

### 3. On-Demand To-Do List & Timed Alerts
- **Quick Reminders:**
  - Specific time today/tomorrow: `/remind 18:30 Call client`
  - Specific date and time: `/remind 2026-11-20 14:00 Submit project`
  - Relative duration: `/in 45m Check oven` or `/in 2h Gym workout`
- **Quick Today's To-Do:**
  - `/todo Buy groceries` or via the persistent `➕ Quick Todo` reply button.
- **Interactive Alert & Snooze:**
  - When alert fires: `🔔 REMINDER: [Task Text]`
  - Buttons: `✅ Done`, `💤 +15 min`, `💤 +1 hour`.
- **Bot Restart Persistence:**
  - All reminders and scheduled jobs are saved in SQLite and reloaded into APScheduler upon startup.
  - If a reminder was missed while the bot was restarting or offline, it triggers automatically upon boot!
- **Interactive Dashboard (`/plan` or `📅 My Planner`):**
  - View Today's and Tomorrow's tasks and upcoming timed reminders.
  - Mark tasks done, add tasks, or delete tasks with inline buttons.

---

### 4. Security & Middleware
- Strict single-admin restriction via `AdminMiddleware`.
- Unauthorized users receive an immediate `⛔ Access Denied` message and cannot access any data or commands.

---

## 📁 Project Architecture

```
telegram_planner_bot/
├── .env.example          # Environment variables template
├── requirements.txt      # Python dependencies
├── README.md             # Documentation
├── config.py             # Settings, datetime helpers, and currency formatters
├── database.py           # Async SQLite database layer using aiosqlite
├── keyboards.py          # Reply and Inline keyboards
├── middlewares.py        # Security admin authorization middleware
├── scheduler.py          # APScheduler configuration (21:00, 09:00 & reminder jobs)
├── handlers/
│   ├── __init__.py       # Router aggregations
│   ├── common.py         # /start, /help, /cancel, Today's Summary
│   ├── expenses.py       # Expense logging, 70k alert, reports, CSV export
│   └── planner.py        # Nightly planning, morning briefing, /todo, /remind, /in, /plan
└── main.py               # Main bot execution entry point
```

---

## 🚀 Setup & Installation

### Step 1: Clone or Navigate to the Project Directory
```bash
cd telegram_planner_bot
```

### Step 2: Create and Activate a Virtual Environment
```bash
# On Linux / macOS
python3 -m venv venv
source venv/bin/activate

# On Windows
python -m venv venv
venv\Scripts\activate
```

### Step 3: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 4: Configure Environment Variables (`.env`)
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Edit `.env` with your credentials:
```ini
# Bot Token from @BotFather
BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ_123456

# Your numeric Telegram User ID (obtain from @userinfobot)
ADMIN_USER_ID=123456789

# Daily spending threshold in UZS
DAILY_BUDGET_LIMIT=70000

# Scheduled loop times (Asia/Tashkent time)
EVENING_PLAN_HOUR=21
EVENING_PLAN_MINUTE=0
MORNING_BRIEF_HOUR=9
MORNING_BRIEF_MINUTE=0
```

### Step 5: Run the Bot
```bash
python main.py
```

---

## 💬 Usage Cheat Sheet

| Feature | Input / Command | Description |
|---|---|---|
| **Quick Expense** | `45000 lunch` | Logs 45,000 UZS for lunch |
| **Shorthand Expense** | `12k taxi` | Logs 12,000 UZS for taxi (`k` = 1,000) |
| **Undo Expense** | Tap `❌ Undo / Delete` | Reverts and deletes the logged expense |
| **Expense Reports** | `/expenses` or `💸 Expenses` | Opens inline filter menu (Today, Week, Month, 2 Months) |
| **Date Breakdown** | `/spent 2026-11-14` | Shows spending breakdown for specific date |
| **Date Range Breakdown** | `/spent 2026-11-01 2026-11-14` | Shows spending breakdown between two dates |
| **Export Data** | `/export` | Downloads `.csv` file of all expenses |
| **Daily Planner** | `/plan` or `📅 My Planner` | Opens Today & Tomorrow task dashboard |
| **Evening Planning** | `/plan_tomorrow` | Interactive multi-line planning for tomorrow |
| **Today's To-Do** | `/todo Buy groceries` | Adds a to-do item for today |
| **Timed Reminder** | `/remind 18:30 Call client` | Sets reminder for 18:30 today (or tomorrow if past) |
| **Calendar Reminder** | `/remind 2026-11-20 14:00 Submit report` | Sets reminder for specific calendar date and time |
| **Relative Timer** | `/in 45m Check oven` | Sets reminder in 45 minutes |
| **Summary** | `📊 Today's Summary` | Instant overview of today's spending & tasks |
| **Cancel** | `/cancel` | Aborts any active input or FSM state |
