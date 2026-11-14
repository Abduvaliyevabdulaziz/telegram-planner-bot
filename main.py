"""
Main entry point for the Personal Expense Tracker & Smart Daily Planner Telegram Bot.
Initializes aiogram 3.x, SQLite (aiosqlite), APScheduler, and security middleware.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

# Compatibility for DefaultBotProperties across aiogram 3.x minor releases
try:
    from aiogram.client.default import DefaultBotProperties
    DEFAULT_PROPERTIES = DefaultBotProperties(parse_mode=ParseMode.HTML)
except ImportError:
    DEFAULT_PROPERTIES = None

from config import settings, TIMEZONE, format_currency
from database import Database
from handlers import common_router, expenses_router, planner_router
from middlewares import AdminMiddleware
from scheduler import (
    create_scheduler,
    register_fixed_cron_jobs,
    restore_persisted_reminders,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


async def on_startup(bot: Bot, db: Database, scheduler) -> None:
    """Startup initialization tasks."""
    logger.info("Initializing database...")
    await db.init_db()

    logger.info("Registering APScheduler fixed cron jobs (21:00 & 09:00)...")
    register_fixed_cron_jobs(scheduler, bot, db, settings.ADMIN_USER_ID)

    logger.info("Restoring persisted reminders from database into APScheduler...")
    restored_count = await restore_persisted_reminders(
        scheduler, bot, db, settings.ADMIN_USER_ID
    )

    logger.info("Starting APScheduler...")
    scheduler.start()

    logger.info("Sending startup notification to admin...")
    try:
        limit_fmt = format_currency(settings.DAILY_BUDGET_LIMIT)
        await bot.send_message(
            chat_id=settings.ADMIN_USER_ID,
            text=(
                "🤖 <b>Bot started and active!</b>\n"
                f"• Timezone: <b>{settings.TIMEZONE_NAME} (UTC+5)</b>\n"
                f"• Daily Budget Limit: <b>{limit_fmt}</b>\n"
                f"• Restored Active Reminders: <b>{restored_count}</b>\n\n"
                "Ready to track expenses and plan your day. Send /help for command list."
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(
            f"Could not send startup message to admin ({settings.ADMIN_USER_ID}): {e}. "
            "Ensure you have sent at least one message to the bot first."
        )


async def start_health_server(port: int = 8080) -> web.AppRunner:
    """
    Starts a lightweight HTTP server for cloud platforms (Render, Koyeb, Railway).
    Allows free cloud hosting with zero-downtime health checking.
    """
    app = web.Application()

    async def health(request: web.Request) -> web.Response:
        return web.Response(
            text=(
                "🤖 Telegram Expense Tracker & Smart Daily Planner Bot is LIVE!\n"
                f"• Timezone: {settings.TIMEZONE_NAME}\n"
                f"• Status: Healthy & Polling 24/7\n"
            ),
            content_type="text/plain",
        )

    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_get("/ping", health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Cloud health-check HTTP server listening on 0.0.0.0:{port}")
    return runner


async def on_shutdown(bot: Bot, scheduler, web_runner: web.AppRunner | None = None) -> None:
    """Graceful shutdown actions."""
    if web_runner:
        logger.info("Stopping HTTP health server...")
        await web_runner.cleanup()

    logger.info("Shutting down APScheduler...")
    if scheduler.running:
        scheduler.shutdown(wait=False)

    logger.info("Closing bot session...")
    await bot.session.close()
    logger.info("Bot stopped successfully.")


async def main() -> None:
    # 1. Validate environment configuration
    try:
        settings.validate()
    except ValueError as e:
        logger.error(f"Configuration Error: {e}")
        sys.exit(1)

    logger.info(
        f"Starting bot with ADMIN_USER_ID={settings.ADMIN_USER_ID}, "
        f"TIMEZONE={settings.TIMEZONE_NAME}, "
        f"DAILY_LIMIT={settings.DAILY_BUDGET_LIMIT} UZS"
    )

    # 2. Initialize Bot instance
    if DEFAULT_PROPERTIES is not None:
        bot = Bot(token=settings.BOT_TOKEN, default=DEFAULT_PROPERTIES)
    else:
        bot = Bot(token=settings.BOT_TOKEN, parse_mode=ParseMode.HTML)

    # 3. Storage and Dispatcher
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # 4. Initialize Database and Scheduler
    db = Database(settings.DB_PATH)
    scheduler = create_scheduler()

    # 5. Dependency Injection for Handlers (Workflow Data)
    dp["db"] = db
    dp["scheduler"] = scheduler

    # 6. Register Security Middleware (Blocks all unauthorized users)
    admin_middleware = AdminMiddleware(settings.ADMIN_USER_ID)
    dp.message.outer_middleware(admin_middleware)
    dp.callback_query.outer_middleware(admin_middleware)

    # 7. Register Routers in precedence order
    # Common commands (/start, /help, /cancel)
    dp.include_router(common_router)
    # Planner commands and FSM states (/plan, /todo, /remind, /in, night planning)
    dp.include_router(planner_router)
    # Expense commands and natural text handler
    dp.include_router(expenses_router)

    # 8. Run startup lifecycle
    await on_startup(bot, db, scheduler)

    # 9. Start cloud HTTP health server (enables Render Free Web Service & Koyeb 24/7)
    web_runner: web.AppRunner | None = None
    try:
        http_port = int(os.getenv("PORT", "8080"))
        web_runner = await start_health_server(http_port)
    except Exception as e:
        logger.warning(f"Could not bind HTTP health port: {e}. Polling will continue normally.")

    # 10. Start polling
    try:
        # Delete webhook to prevent conflicts with long polling
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await on_shutdown(bot, scheduler, web_runner)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot execution terminated by user.")
