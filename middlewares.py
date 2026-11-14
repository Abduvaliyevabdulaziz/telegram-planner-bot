"""
Security Middlewares for aiogram 3.
Ensures only the authorized ADMIN_USER_ID can interact with the bot.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

logger = logging.getLogger(__name__)


class AdminMiddleware(BaseMiddleware):
    """
    Middleware that enforces single-user administrative access.
    Blocks any Telegram user whose ID does not match ADMIN_USER_ID.
    """

    def __init__(self, admin_user_id: int) -> None:
        super().__init__()
        self.admin_user_id = admin_user_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")

        if not user or user.id != self.admin_user_id:
            user_id_str = str(user.id) if user else "Unknown"
            username_str = f"@{user.username}" if (user and user.username) else "NoUsername"
            logger.warning(
                f"Unauthorized access attempt by user_id={user_id_str} ({username_str})"
            )

            # Silently or politely drop unauthorized updates
            if isinstance(event, Message):
                await event.answer(
                    "⛔ <b>Access Denied</b>\n"
                    "This bot is configured strictly for private personal use. "
                    "Your user ID is not authorized.",
                    parse_mode="HTML",
                )
            elif isinstance(event, CallbackQuery):
                await event.answer(
                    "⛔ Access Denied: You are not authorized to use this bot.",
                    show_alert=True,
                )
            return None

        # Authorized admin: proceed to handler
        return await handler(event, data)
