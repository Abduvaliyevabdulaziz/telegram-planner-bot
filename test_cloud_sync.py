"""
Unit tests for CloudSyncManager.
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import asyncio

from cloud_sync import CloudSyncManager


class TestCloudSyncManager(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mock_bot = MagicMock()
        self.mock_bot.get_chat = AsyncMock()
        self.mock_bot.get_file = AsyncMock()
        self.mock_bot.download_file = AsyncMock()
        self.mock_bot.send_document = AsyncMock()
        self.mock_bot.pin_chat_message = AsyncMock()
        self.mock_bot.unpin_chat_message = AsyncMock()
        self.mock_bot.delete_message = AsyncMock()

        self.test_db = Path("test_sync_unit.db")
        self.test_db.write_text("DUMMY_DB_DATA", encoding="utf-8")
        self.admin_id = 123456789
        self.manager = CloudSyncManager(self.mock_bot, self.test_db, self.admin_id)

    async def asyncTearDown(self):
        if self.test_db.exists():
            self.test_db.unlink()
        tmp = self.test_db.with_suffix(".restore_tmp")
        if tmp.exists():
            tmp.unlink()

    async def test_restore_no_pinned_message(self):
        mock_chat = MagicMock()
        mock_chat.pinned_message = None
        self.mock_bot.get_chat.return_value = mock_chat

        result = await self.manager.restore_if_needed()
        self.assertFalse(result)

    async def test_restore_non_db_document(self):
        mock_chat = MagicMock()
        mock_doc = MagicMock()
        mock_doc.file_name = "image.png"
        mock_chat.pinned_message.document = mock_doc
        self.mock_bot.get_chat.return_value = mock_chat

        result = await self.manager.restore_if_needed()
        self.assertFalse(result)

    async def test_restore_valid_db(self):
        mock_chat = MagicMock()
        mock_doc = MagicMock()
        mock_doc.file_name = "planner_bot.db"
        mock_doc.file_id = "test_file_id_xyz"
        mock_chat.pinned_message.document = mock_doc
        mock_chat.pinned_message.message_id = 999
        self.mock_bot.get_chat.return_value = mock_chat

        mock_file = MagicMock()
        mock_file.file_path = "documents/planner_bot.db"
        self.mock_bot.get_file.return_value = mock_file

        async def fake_download(path, target):
            target.write_text("RESTORED_DB_CONTENT", encoding="utf-8")

        self.mock_bot.download_file.side_effect = fake_download

        result = await self.manager.restore_if_needed()
        self.assertTrue(result)
        self.assertEqual(self.test_db.read_text(encoding="utf-8"), "RESTORED_DB_CONTENT")

    async def test_sync_now(self):
        mock_msg = MagicMock()
        mock_msg.message_id = 501
        self.mock_bot.send_document.return_value = mock_msg

        await self.manager.sync_now()

        self.mock_bot.send_document.assert_called_once()
        self.mock_bot.pin_chat_message.assert_called_once_with(
            chat_id=self.admin_id,
            message_id=501,
            disable_notification=True,
        )


if __name__ == "__main__":
    unittest.main()
