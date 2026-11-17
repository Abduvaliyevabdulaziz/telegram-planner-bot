"""
Cloud Persistence Manager for Telegram Bot.
Automatically backs up and restores the SQLite database using Telegram cloud storage.
Guarantees permanent data persistence across Render container restarts, sleep cycles, and cold starts.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING
from aiogram import Bot
from aiogram.types import FSInputFile

if TYPE_CHECKING:
    from database import Database

logger = logging.getLogger(__name__)


class CloudSyncManager:
    def __init__(self, bot: Bot, db_path: Path | str, admin_user_id: int) -> None:
        self.bot = bot
        self.db_path = Path(db_path)
        self.admin_user_id = admin_user_id
        self._last_backup_message_id: int | None = None
        self._sync_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def restore_if_needed(self) -> bool:
        """
        On bot cold start, check if the local database exists and has data.
        If empty or missing, retrieve the latest pinned backup document from Telegram.
        """
        try:
            logger.info(f"Checking Telegram chat {self.admin_user_id} for existing cloud backups...")
            chat = await self.bot.get_chat(self.admin_user_id)
            if not chat.pinned_message:
                logger.info("No pinned message found in chat. Starting with fresh database.")
                return False

            doc = chat.pinned_message.document
            if not doc or not doc.file_name or not doc.file_name.endswith(".db"):
                logger.info("Pinned message does not contain a database file (.db). Skipping restore.")
                return False

            self._last_backup_message_id = chat.pinned_message.message_id
            logger.info(
                f"Found pinned cloud backup (message_id={self._last_backup_message_id}, "
                f"file_id={doc.file_id}). Restoring..."
            )

            tg_file = await self.bot.get_file(doc.file_id)
            if not tg_file.file_path:
                logger.warning("Telegram file path is empty. Could not download.")
                return False

            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_db = self.db_path.with_suffix(".restore_tmp")
            await self.bot.download_file(tg_file.file_path, tmp_db)

            if tmp_db.exists() and tmp_db.stat().st_size > 0:
                if self.db_path.exists():
                    try:
                        self.db_path.unlink()
                    except Exception:
                        pass
                tmp_db.replace(self.db_path)
                logger.info(
                    f"✅ Database successfully restored from Telegram cloud backup! "
                    f"Size: {self.db_path.stat().st_size} bytes."
                )
                return True

        except Exception as e:
            logger.warning(f"Could not restore database from Telegram backup: {e}")
        return False

    def schedule_sync(self, delay: float = 1.5) -> None:
        """
        Debounced schedule of database upload to Telegram cloud.
        Batches rapid consecutive writes within `delay` seconds.
        """
        try:
            loop = asyncio.get_running_loop()
            if self._sync_task and not self._sync_task.done():
                self._sync_task.cancel()
            self._sync_task = loop.create_task(self._debounced_sync(delay))
        except RuntimeError:
            pass

    async def _debounced_sync(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            await self.sync_now()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in debounced cloud sync: {e}")

    async def sync_now(self) -> None:
        """Upload current SQLite database to Telegram as a silent pinned backup."""
        async with self._lock:
            if not self.db_path.exists() or self.db_path.stat().st_size == 0:
                return

            try:
                # 1. Send silent document to admin chat
                new_msg = await self.bot.send_document(
                    chat_id=self.admin_user_id,
                    document=FSInputFile(self.db_path, filename="planner_bot.db"),
                    caption="💾 <b>Data Auto-Saved</b> <i>(Permanent Cloud Storage)</i>",
                    parse_mode="HTML",
                    disable_notification=True,
                )

                # 2. Pin new message (silent, no push alert)
                await self.bot.pin_chat_message(
                    chat_id=self.admin_user_id,
                    message_id=new_msg.message_id,
                    disable_notification=True,
                )

                old_id = self._last_backup_message_id
                self._last_backup_message_id = new_msg.message_id

                # 3. Clean up the previous backup message so the chat stays clean
                if old_id and old_id != new_msg.message_id:
                    try:
                        await self.bot.unpin_chat_message(
                            chat_id=self.admin_user_id,
                            message_id=old_id,
                        )
                        await self.bot.delete_message(
                            chat_id=self.admin_user_id,
                            message_id=old_id,
                        )
                    except Exception:
                        pass

                logger.info(
                    f"✅ Database successfully synced to Telegram cloud backup (msg_id={new_msg.message_id})."
                )

            except Exception as e:
                logger.error(f"Failed to sync database to Telegram: {e}")
