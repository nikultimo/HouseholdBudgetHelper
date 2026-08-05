import pytest
from unittest.mock import AsyncMock, MagicMock, patch, mock_open
import base64


def _make_txn(confidence=0.95):
    from datetime import date
    from llm.schemas import TransactionInput
    return TransactionInput(
        date=date(2026, 4, 17),
        description="Coffee",
        category="Food",
        type="Расход",
        whose="User1",
        amount=200.0,
        account="Main Card",
        mandatory="Нет",
        confidence=confidence,
        reasoning="clear receipt",
    )


@pytest.mark.asyncio
async def test_photo_handler_sends_confirm_on_success():
    from handlers.photo import handle_photo_receipt

    update = MagicMock()
    update.message.chat.send_action = AsyncMock()
    update.message.reply_text = AsyncMock()
    update.effective_user.id = 123
    update.message.photo = [MagicMock(file_id="fid1")]

    context = MagicMock()
    mock_file = AsyncMock()
    mock_file.download_to_drive = AsyncMock()
    context.bot.get_file = AsyncMock(return_value=mock_file)

    llm_client = MagicMock()
    llm_client.chat_vision = AsyncMock(return_value=_make_txn(0.95))

    reader = MagicMock()
    reader.get_categories.return_value = ["Food"]
    reader.get_accounts.return_value = ["Main Card"]

    cfg = MagicMock()
    cfg.confidence_threshold = 0.8
    cfg.vision_model = "google/gemini-2.5-flash"
    cfg.default_user = "User1"

    pending = {}

    with patch("builtins.open", mock_open(read_data=b"fakejpeg")), \
         patch("os.path.exists", return_value=False), \
         patch("tempfile.mktemp", return_value="/tmp/fake.jpg"):
        await handle_photo_receipt(update, context, llm_client=llm_client, reader=reader, cfg=cfg, pending=pending)

    update.message.reply_text.assert_called_once()
    assert len(pending) == 1
    call_text = update.message.reply_text.call_args[0][0]
    assert "Coffee" in call_text


@pytest.mark.asyncio
async def test_photo_handler_replies_error_on_vision_failure():
    from handlers.photo import handle_photo_receipt

    update = MagicMock()
    update.message.chat.send_action = AsyncMock()
    update.message.reply_text = AsyncMock()
    update.effective_user.id = 456
    update.message.photo = [MagicMock(file_id="fid2")]

    context = MagicMock()
    mock_file = AsyncMock()
    mock_file.download_to_drive = AsyncMock()
    context.bot.get_file = AsyncMock(return_value=mock_file)

    llm_client = MagicMock()
    llm_client.chat_vision = AsyncMock(side_effect=Exception("API error"))

    reader = MagicMock()
    reader.get_categories.return_value = []
    reader.get_accounts.return_value = []

    cfg = MagicMock()
    cfg.confidence_threshold = 0.8
    cfg.vision_model = "google/gemini-2.5-flash"
    cfg.default_user = "User1"

    pending = {}

    with patch("builtins.open", mock_open(read_data=b"fakejpeg")), \
         patch("os.path.exists", return_value=False), \
         patch("tempfile.mktemp", return_value="/tmp/fake.jpg"):
        await handle_photo_receipt(update, context, llm_client=llm_client, reader=reader, cfg=cfg, pending=pending)

    call_text = update.message.reply_text.call_args[0][0]
    assert "распознать" in call_text.lower()
    assert 456 not in pending


@pytest.mark.asyncio
async def test_two_pending_transactions_use_different_keys():
    """Second transaction must not overwrite first in _pending."""
    from handlers.photo import handle_photo_receipt

    def _make_update(user_id: int):
        update = MagicMock()
        update.message.chat.send_action = AsyncMock()
        update.message.reply_text = AsyncMock()
        update.effective_user.id = user_id
        update.message.photo = [MagicMock(file_id=f"fid{user_id}")]
        return update

    context = MagicMock()
    mock_file = AsyncMock()
    mock_file.download_to_drive = AsyncMock()
    context.bot.get_file = AsyncMock(return_value=mock_file)

    llm_client = MagicMock()
    llm_client.chat_vision = AsyncMock(return_value=_make_txn(0.95))

    reader = MagicMock()
    reader.get_categories.return_value = ["Food"]
    reader.get_accounts.return_value = ["Main Card"]

    cfg = MagicMock()
    cfg.confidence_threshold = 0.8
    cfg.vision_model = "google/gemini-2.5-flash"
    cfg.default_user = "User1"

    pending = {}

    with patch("builtins.open", mock_open(read_data=b"fakejpeg")), \
         patch("os.path.exists", return_value=False), \
         patch("tempfile.mktemp", return_value="/tmp/fake.jpg"):
        await handle_photo_receipt(
            _make_update(111), context, llm_client=llm_client,
            reader=reader, cfg=cfg, pending=pending,
        )
        await handle_photo_receipt(
            _make_update(111), context, llm_client=llm_client,
            reader=reader, cfg=cfg, pending=pending,
        )

    assert len(pending) == 2, "each confirmation must get its own key"
