from __future__ import annotations
import asyncio
import base64
import os
import tempfile
import time
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from llm.schemas import TransactionInput
from llm.prompts import build_transaction_prompt
from llm.tracing import start_trace
from handlers.transaction import format_transaction_for_display

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes
    from llm.client import LLMClient
    from excel.reader import ExcelReader
    from config import Config


async def handle_photo_receipt(
    update: "Update",
    context: "ContextTypes.DEFAULT_TYPE",
    llm_client: "LLMClient",
    reader: "ExcelReader",
    cfg: "Config",
    pending: dict,
) -> None:
    await update.message.chat.send_action("typing")
    user_id = str(update.effective_user.id)
    today = datetime.now().strftime("%Y-%m-%d")
    trace_ctx = start_trace(user_id=user_id, input_text="[photo]")

    try:
        photo = update.message.photo[-1]
        tmp_path = tempfile.mktemp(suffix=".jpg")
        try:
            tg_file = await context.bot.get_file(photo.file_id)
            await tg_file.download_to_drive(tmp_path)
            def _read_b64(path: str) -> str:
                with open(path, "rb") as f:
                    return base64.b64encode(f.read()).decode()
            image_b64 = await asyncio.to_thread(_read_b64, tmp_path)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        categories = reader.get_categories()
        accounts = reader.get_accounts()
        prompt = build_transaction_prompt(
            text="[Receipt image — extract transaction details]",
            categories=categories,
            accounts=accounts,
            default_user=cfg.default_user,
            today=today,
        )

        try:
            txn: TransactionInput = await llm_client.chat_vision(
                image_b64=image_b64,
                text_prompt=prompt,
                response_model=TransactionInput,
                vision_model=cfg.vision_model,
                trace_ctx=trace_ctx,
                span_name="parse_photo",
            )
        except Exception:
            await update.message.reply_text("Не удалось распознать чек, попробуй ещё раз.")
            return

        if txn.confidence < cfg.confidence_threshold:
            msg = (
                f"Не уверен в разборе чека (уверенность: {txn.confidence:.0%}).\n\n"
                + format_transaction_for_display(txn)
                + "\n\nВсё верно?"
            )
        else:
            msg = format_transaction_for_display(txn) + "\n\nДобавить транзакцию?"

        token = uuid.uuid4().hex
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Да", callback_data=f"confirm:{token}"),
            InlineKeyboardButton("❌ Нет", callback_data=f"cancel:{token}"),
        ]])
        pending[token] = (txn, time.monotonic())
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=keyboard)
    finally:
        trace_ctx.finish()
