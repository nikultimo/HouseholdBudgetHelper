import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from datetime import date
from llm.schemas import TransactionInput


def _make_txn():
    return TransactionInput(
        date=date(2026, 4, 20),
        description="кофе",
        category="Еда",
        type="Расход",
        whose="User1",
        amount=200.0,
        account="Main Card",
        mandatory="Нет",
        confidence=0.95,
        reasoning="test",
    )


@pytest.mark.asyncio
async def test_on_callback_edits_message_before_upload():
    """User must see confirmation message before yadisk upload finishes."""
    import bot as bot_module

    token = "testtoken123"
    bot_module._pending[token] = _make_txn()

    upload_started = asyncio.Event()
    upload_may_finish = asyncio.Event()

    async def slow_upload():
        upload_started.set()
        await upload_may_finish.wait()

    query = MagicMock()
    query.data = f"confirm:{token}"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message.reply_text = AsyncMock()

    update = MagicMock()
    update.effective_user.id = 99
    update.callback_query = query

    context = MagicMock()

    mock_backup = MagicMock()
    mock_writer = MagicMock()
    mock_reader = MagicMock()
    mock_yadisk = MagicMock()
    mock_yadisk.upload = slow_upload

    orig_backup = bot_module.backup_manager
    orig_writer = bot_module.writer
    orig_reader = bot_module.reader
    orig_yadisk = bot_module.yadisk
    orig_cfg = bot_module.cfg

    bot_module.backup_manager = mock_backup
    bot_module.writer = mock_writer
    bot_module.reader = mock_reader
    bot_module.yadisk = mock_yadisk
    bot_module.cfg = MagicMock(allowed_users=[99])

    try:
        task = asyncio.create_task(bot_module.on_callback(update, context))
        await upload_started.wait()
        # Upload is running but not done — message must already be edited
        query.edit_message_text.assert_called_once()
        upload_may_finish.set()
        await task
    finally:
        bot_module.backup_manager = orig_backup
        bot_module.writer = orig_writer
        bot_module.reader = orig_reader
        bot_module.yadisk = orig_yadisk
        bot_module.cfg = orig_cfg


@pytest.mark.asyncio
async def test_upload_or_notify_sends_message_on_failure():
    """On upload failure, user must receive a retry-hint reply."""
    from bot import _upload_or_notify

    query = MagicMock()
    query.message.reply_text = AsyncMock()

    yadisk = MagicMock()
    yadisk.upload = AsyncMock(side_effect=Exception("disk error"))

    await _upload_or_notify(query, yadisk)

    query.message.reply_text.assert_called_once()
    assert "Яндекс Диск" in query.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_delete_select_shows_delete_confirm_buttons():
    import bot as bot_module

    query = MagicMock()
    query.data = "delete_select:12"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()

    update = MagicMock()
    update.effective_user.id = 99
    update.callback_query = query

    mock_reader = MagicMock()
    mock_reader.get_transactions.return_value = [{
        "row": 12,
        "date": date(2026, 4, 20),
        "description": "кофе",
        "category": "Еда",
        "type": "Расход",
        "whose": "User1",
        "amount": 200.0,
        "account": "Main Card",
        "mandatory": "Нет",
    }]

    orig_reader = bot_module.reader
    orig_cfg = bot_module.cfg
    bot_module.reader = mock_reader
    bot_module.cfg = MagicMock(allowed_users=[99])

    try:
        await bot_module.on_callback(update, MagicMock())
    finally:
        bot_module.reader = orig_reader
        bot_module.cfg = orig_cfg

    kwargs = query.edit_message_text.call_args.kwargs
    assert "Удалить эту транзакцию" in query.edit_message_text.call_args.args[0]
    assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "delete_confirm:12"
    assert kwargs["reply_markup"].inline_keyboard[0][1].callback_data == "delete_cancel:0"


@pytest.mark.asyncio
async def test_delete_confirm_deletes_row_instead_of_editing():
    import bot as bot_module

    query = MagicMock()
    query.data = "delete_confirm:12"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message.reply_text = AsyncMock()

    update = MagicMock()
    update.effective_user.id = 99
    update.callback_query = query

    mock_backup = MagicMock()
    mock_writer = MagicMock()
    mock_reader = MagicMock()
    mock_yadisk = MagicMock()
    mock_yadisk.upload = AsyncMock()

    orig_backup = bot_module.backup_manager
    orig_writer = bot_module.writer
    orig_reader = bot_module.reader
    orig_yadisk = bot_module.yadisk
    orig_cfg = bot_module.cfg

    bot_module.backup_manager = mock_backup
    bot_module.writer = mock_writer
    bot_module.reader = mock_reader
    bot_module.yadisk = mock_yadisk
    bot_module.cfg = MagicMock(allowed_users=[99])

    try:
        await bot_module.on_callback(update, MagicMock())
        await asyncio.sleep(0)
    finally:
        bot_module.backup_manager = orig_backup
        bot_module.writer = orig_writer
        bot_module.reader = orig_reader
        bot_module.yadisk = orig_yadisk
        bot_module.cfg = orig_cfg

    mock_writer.reverse_transaction.assert_called_once_with("12")
    mock_writer.correct_transaction.assert_not_called()


@pytest.mark.asyncio
async def test_edit_field_prompt_shows_field_and_current_value():
    import bot as bot_module
    from handlers.edit import clear_pending_edit, set_pending_edit

    user_id = "99"
    set_pending_edit(user_id, {
        "mode": "edit",
        "row": 12,
        "txn": {
            "row": 12,
            "date": date(2026, 4, 20),
            "description": "кофе",
            "category": "Еда",
            "type": "Расход",
            "whose": "User1",
            "amount": 200.0,
            "account": "Main Card",
            "mandatory": "Нет",
        },
        "field": None,
    })

    query = MagicMock()
    query.data = "edit_field:amount"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()

    update = MagicMock()
    update.effective_user.id = 99
    update.callback_query = query

    orig_cfg = bot_module.cfg
    bot_module.cfg = MagicMock(allowed_users=[99])

    try:
        await bot_module.on_callback(update, MagicMock())
    finally:
        clear_pending_edit(user_id)
        bot_module.cfg = orig_cfg

    text = query.edit_message_text.call_args.args[0]
    assert "Редактируется: <b>Сумма</b>" in text
    assert "Текущее значение: <b>200 ₽</b>" in text
