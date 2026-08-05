from __future__ import annotations
import asyncio
import logging
import os
import re
import tempfile
from collections import Counter
from typing import TYPE_CHECKING

from llm.schemas import TransactionInput
from llm.prompts import build_transaction_prompt
from currency.rates import convert_to_rub

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from llm.client import LLMClient
    from llm.tracing import TraceContext
    from excel.reader import ExcelReader
    from telegram import Voice, Bot


def _normalized_description(value: str) -> str:
    value = re.sub(r"\([^)]*\)", "", value.casefold())
    value = re.sub(r"[^a-zа-яё0-9]+", " ", value)
    return " ".join(value.split())


def _dominant_value(values: list[str]) -> str | None:
    nonempty = [value for value in values if value]
    if len(nonempty) < 3:
        return None
    value, count = Counter(nonempty).most_common(1)[0]
    return value if count / len(nonempty) >= 0.8 else None


def _apply_confirmed_history(
    txn: TransactionInput,
    transactions: list[dict],
) -> TransactionInput:
    """Reuse stable category/account choices from confirmed matching entries."""
    normalized = _normalized_description(txn.description)
    matches = [
        item for item in transactions
        if item.get("type") == txn.type
        and _normalized_description(str(item.get("description") or "")) == normalized
    ]
    category = _dominant_value([str(item.get("category") or "") for item in matches])
    account = _dominant_value([str(item.get("account") or "") for item in matches])
    updates = {}
    if category:
        updates["category"] = category
    if account:
        updates["account"] = account
    if not updates:
        return txn
    updates["reasoning"] = (
        txn.reasoning
        + " Stable defaults were reused from at least three confirmed matching entries."
    )
    return txn.model_copy(update=updates)


async def parse_transaction(
    text: str,
    llm_client: "LLMClient",
    reader: "ExcelReader",
    default_user: str,
    today: str,
    trace_ctx: "TraceContext | None" = None,
) -> TransactionInput:
    categories = reader.get_categories()
    accounts = reader.get_accounts()
    system_prompt = build_transaction_prompt(
        text=text,
        categories=categories,
        accounts=accounts,
        default_user=default_user,
        today=today,
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ]
    txn = await llm_client.chat_structured(
        messages=messages,
        response_model=TransactionInput,
        trace_ctx=trace_ctx,
        span_name="parse_transaction",
    )
    history = await asyncio.to_thread(reader.get_transactions)
    txn = _apply_confirmed_history(txn, history)
    currency = (txn.original_currency or "RUB").strip().upper()
    if currency not in ("RUB", "РУБ", "Р", "₽", ""):
        orig = txn.original_amount if txn.original_amount is not None else txn.amount
        try:
            rub_amount, rate = await convert_to_rub(orig, currency)
            txn.amount = rub_amount
            txn.original_amount = orig
        except ValueError:
            logger.warning("Unknown currency %r — keeping amount as-is", currency)
            txn.original_currency = "RUB"
    return txn


def format_transaction_for_display(txn: TransactionInput) -> str:
    mandatory_icon = "🔒" if txn.mandatory == "Да" else "📝"
    type_icon = "💸" if txn.type == "Расход" else "💰"
    currency = (txn.original_currency or "RUB").strip().upper()
    orig_amount = txn.original_amount if txn.original_amount is not None else txn.amount
    if currency in ("RUB", "РУБ", "Р", "₽", ""):
        currency_line = "Валюта: RUB"
    else:
        rate = txn.amount / orig_amount if orig_amount else 1.0
        currency_line = f"Валюта: {currency} ({orig_amount:,.2f} × {rate:.2f} = {txn.amount:,.0f} ₽)"
    return (
        f"{type_icon} <b>{txn.description}</b>\n"
        f"Сумма: <b>{txn.amount:,.0f} ₽</b>\n"
        f"{currency_line}\n"
        f"Категория: {txn.category}\n"
        f"Дата: {txn.date.strftime('%d.%m.%Y')}\n"
        f"Чья: {txn.whose} · Счёт: {txn.account}\n"
        f"Обязательный: {txn.mandatory} {mandatory_icon}\n"
        f"Уверенность: {txn.confidence:.0%}"
    )


def is_likely_transaction(text: str) -> bool:
    import re
    has_amount = bool(re.search(r'\d+', text))
    question_words = ["сколько", "когда", "почему", "как", "покажи", "расскажи", "сравни", "анализ"]
    is_question = any(w in text.lower() for w in question_words) or text.strip().endswith("?")
    return has_amount and not is_question


async def download_and_transcribe(
    voice: "Voice",
    bot: "Bot",
    llm_client: "LLMClient",
) -> str:
    tmp_path = tempfile.mktemp(suffix=".oga")
    try:
        tg_file = await bot.get_file(voice.file_id)
        await tg_file.download_to_drive(tmp_path)
        text = await llm_client.transcribe(tmp_path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    return text
