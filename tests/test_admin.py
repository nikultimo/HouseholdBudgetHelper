import pytest
from unittest.mock import AsyncMock, MagicMock
import asyncio


@pytest.mark.asyncio
async def test_cmd_last_formats_transactions():
    mock_reader = MagicMock()
    mock_reader.get_last_transactions.return_value = [
        {
            "date": __import__("datetime").date(2026, 4, 16),
            "description": "кофе",
            "category": "Еда и продукты",
            "amount": 200.0,
            "whose": "User1",
            "type": "Расход",
        }
    ]
    mock_update = MagicMock()
    mock_update.message.reply_text = AsyncMock()
    mock_update.message.text = "/last 3"
    mock_context = MagicMock()
    mock_context.args = ["3"]

    from handlers.admin import cmd_last
    await cmd_last(mock_update, mock_context, reader=mock_reader)

    mock_update.message.reply_text.assert_called_once()
    call_text = mock_update.message.reply_text.call_args[0][0]
    assert "кофе" in call_text or "200" in call_text


@pytest.mark.asyncio
async def test_cmd_versions_lists_backups():
    mock_backup = MagicMock()
    mock_backup.list_backups.return_value = [
        {"index": 1, "timestamp": "2026-04-16 10:00:00", "name": "budget_20260416_100000.xlsx"},
        {"index": 2, "timestamp": "2026-04-15 22:00:00", "name": "budget_20260415_220000.xlsx"},
    ]
    mock_update = MagicMock()
    mock_update.message.reply_text = AsyncMock()
    mock_context = MagicMock()

    from handlers.admin import cmd_versions
    await cmd_versions(mock_update, mock_context, backup_manager=mock_backup)

    mock_update.message.reply_text.assert_called_once()
    call_text = mock_update.message.reply_text.call_args[0][0]
    assert "2026-04-16" in call_text


@pytest.mark.asyncio
async def test_cmd_sync_awaits_download_before_reply():
    from handlers.admin import cmd_sync

    download_started = asyncio.Event()
    allow_download_finish = asyncio.Event()

    async def slow_download():
        download_started.set()
        await allow_download_finish.wait()

    mock_yadisk = MagicMock()
    mock_yadisk.download = slow_download

    mock_reader = MagicMock()
    mock_reader.invalidate_cache = MagicMock()

    mock_update = MagicMock()
    mock_update.message.reply_text = AsyncMock()
    mock_context = MagicMock()

    task = asyncio.create_task(
        cmd_sync(mock_update, mock_context, yadisk=mock_yadisk, reader=mock_reader)
    )
    await download_started.wait()

    # Still downloading -> must not claim success yet.
    mock_update.message.reply_text.assert_not_called()

    allow_download_finish.set()
    await task

    mock_reader.invalidate_cache.assert_called_once()
    mock_update.message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_cmd_download_excel_awaits_download_before_sending_document(tmp_path):
    from handlers.admin import cmd_download_excel

    download_started = asyncio.Event()
    allow_download_finish = asyncio.Event()
    local_file = tmp_path / "budget.xlsx"
    local_file.write_bytes(b"xlsx")

    async def slow_download():
        download_started.set()
        await allow_download_finish.wait()

    mock_yadisk = MagicMock()
    mock_yadisk.download = slow_download
    mock_yadisk.local_path = str(local_file)

    mock_reader = MagicMock()
    mock_reader.invalidate_cache = MagicMock()

    mock_update = MagicMock()
    mock_update.message.reply_text = AsyncMock()
    mock_update.message.reply_document = AsyncMock()
    mock_context = MagicMock()

    task = asyncio.create_task(
        cmd_download_excel(mock_update, mock_context, yadisk=mock_yadisk, reader=mock_reader)
    )
    await download_started.wait()

    mock_update.message.reply_text.assert_not_called()
    mock_update.message.reply_document.assert_not_called()

    allow_download_finish.set()
    await task

    mock_reader.invalidate_cache.assert_called_once()
    mock_update.message.reply_text.assert_not_called()
    mock_update.message.reply_document.assert_called_once()
    assert mock_update.message.reply_document.call_args.kwargs["filename"] == "budget.xlsx"


@pytest.mark.asyncio
async def test_cmd_restore_awaits_upload_before_reply():
    from handlers.admin import cmd_restore

    upload_started = asyncio.Event()
    allow_upload_finish = asyncio.Event()

    async def slow_upload():
        upload_started.set()
        await allow_upload_finish.wait()

    mock_yadisk = MagicMock()
    mock_yadisk.upload = slow_upload

    mock_backup = MagicMock()
    mock_backup.restore = MagicMock(return_value="backup.xlsx")

    mock_reader = MagicMock()
    mock_reader.invalidate_cache = MagicMock()

    mock_update = MagicMock()
    mock_update.message.reply_text = AsyncMock()

    mock_context = MagicMock()
    mock_context.args = ["1"]

    task = asyncio.create_task(
        cmd_restore(
            mock_update,
            mock_context,
            backup_manager=mock_backup,
            yadisk=mock_yadisk,
            reader=mock_reader,
        )
    )
    await upload_started.wait()

    # Still uploading -> must not claim success yet.
    mock_update.message.reply_text.assert_not_called()

    allow_upload_finish.set()
    await task

    mock_backup.restore.assert_called_once_with(1)
    mock_reader.invalidate_cache.assert_called_once()
    mock_update.message.reply_text.assert_called_once()
