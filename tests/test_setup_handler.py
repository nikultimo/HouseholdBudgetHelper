from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import ConversationHandler


def _update(user_id=123):
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    return update


def _context():
    ctx = MagicMock()
    ctx.user_data = {}
    ctx.bot = MagicMock()
    return ctx


def _cfg(tmp_path):
    cfg = MagicMock()
    cfg.allowed_users = [123]
    cfg.default_user = "User1"
    cfg.default_account = "Main Card"
    cfg.budget_file_path = str(tmp_path / "budget.xlsx")
    cfg.salary_pay_days = (5, 20)
    cfg.salary_payment_model = "working_days"
    cfg.salary_payment_percentages = (0.5, 0.5)
    return cfg


@pytest.mark.asyncio
async def test_setup_rejects_when_allowed_users_empty(tmp_path):
    from handlers.setup import cmd_setup

    cfg = _cfg(tmp_path)
    cfg.allowed_users = []
    update = _update()
    state = await cmd_setup(update, _context(), cfg=cfg)

    assert state == ConversationHandler.END
    assert "allowed_users" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_setup_confirm_writes_uploads_and_invalidates(tmp_path):
    from handlers.setup import setup_confirm

    cfg = _cfg(tmp_path)
    reader = MagicMock()
    yadisk = MagicMock()
    yadisk.upload = AsyncMock()
    ctx = _context()
    ctx.user_data["setup"] = {
        "user_name": "Tester",
        "default_account": "Debit",
        "salary": 90000,
    }
    update = _update()

    with patch("handlers.setup.apply_workbook_setup") as apply_setup:
        state = await setup_confirm(update, ctx, cfg=cfg, reader=reader, yadisk=yadisk)

    assert state == ConversationHandler.END
    apply_setup.assert_called_once()
    reader.invalidate_cache.assert_called_once()
    yadisk.upload.assert_awaited_once()
    assert "setup" not in ctx.user_data


@pytest.mark.asyncio
async def test_setup_import_document_parses_csv(tmp_path):
    from handlers.setup import CONFIRM, setup_import_document

    src = tmp_path / "payments.csv"
    src.write_text("Rent,5,30000\nPhone,20,1000\ncategories: Food, Transport\n", encoding="utf-8")

    async def download_to_drive(path):
        with open(src, "rb") as source, open(path, "wb") as dest:
            dest.write(source.read())

    tg_file = MagicMock()
    tg_file.download_to_drive = AsyncMock(side_effect=download_to_drive)

    update = _update()
    update.message.document.file_name = "payments.csv"
    update.message.document.file_id = "file-id"
    ctx = _context()
    ctx.user_data["setup"] = {}
    ctx.bot.get_file = AsyncMock(return_value=tg_file)

    state = await setup_import_document(update, ctx)

    assert state == CONFIRM
    payments = ctx.user_data["setup"]["mandatory_payments"]
    assert [p.description for p in payments] == ["Rent", "Phone"]
    assert ctx.user_data["setup"]["categories"] == ["Food", "Transport"]


@pytest.mark.asyncio
async def test_setup_import_photo_uses_vision_extraction(tmp_path):
    from handlers.setup import CONFIRM, SetupImportExtraction, setup_import_photo

    async def download_to_drive(path):
        with open(path, "wb") as f:
            f.write(b"image")

    tg_file = MagicMock()
    tg_file.download_to_drive = AsyncMock(side_effect=download_to_drive)

    update = _update()
    update.message.photo = [MagicMock(file_id="photo-id")]
    ctx = _context()
    ctx.user_data["setup"] = {}
    ctx.bot.get_file = AsyncMock(return_value=tg_file)

    cfg = _cfg(tmp_path)
    cfg.vision_model = "vision-model"
    llm_client = MagicMock()
    llm_client.chat_vision = AsyncMock(
        return_value=SetupImportExtraction(
            categories=["Food"],
            accounts=["Debit"],
            mandatory_payments=[{"description": "Rent", "due_day": 5, "amount": 30000}],
        )
    )

    state = await setup_import_photo(update, ctx, cfg=cfg, llm_client=llm_client)

    assert state == CONFIRM
    llm_client.chat_vision.assert_awaited_once()
    assert ctx.user_data["setup"]["categories"] == ["Food"]
    assert ctx.user_data["setup"]["accounts"] == ["Debit"]
    assert ctx.user_data["setup"]["mandatory_payments"][0].description == "Rent"
