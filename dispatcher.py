"""Intent dispatcher: routes classified intents to the appropriate handlers."""
from __future__ import annotations
import asyncio
import logging
import re
import time
import uuid
from typing import TYPE_CHECKING

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from handlers import settings_cmd
from handlers.transaction import parse_transaction, format_transaction_for_display
from llm.agent import run_budget_agent
from handlers.query import answer_schema_guided_query
from llm.router import (
    has_transaction_amount,
    is_expense_date_range_query,
    is_leave_pay_query,
)
from llm.ru_months import MONTH_ALTERNATION
from llm.tips_loader import load_tips
from telegram_helpers import reply_to_update

if TYPE_CHECKING:
    from config import Config
    from excel.reader import ExcelReader
    from excel.writer import ExcelWriter
    from llm.client import LLMClient
    from llm.tracing import TraceContext
    from yadisk.sync import YadiskSync

logger = logging.getLogger(__name__)

_DATE_REFERENCE_RE = re.compile(
    rf"\d|\b(?:вчера|позавчера|{MONTH_ALTERNATION})\b",
    re.IGNORECASE,
)
_BALANCE_WORD_RE = re.compile(r"\b(?:денег|баланс|капитал|остаток)\b", re.IGNORECASE)
_NAMED_MONTH_RE = re.compile(rf"\b(?:{MONTH_ALTERNATION})\b", re.IGNORECASE)


def _is_workbook_read_error(exc: Exception) -> bool:
    return isinstance(exc, ValueError) and "workbook" in str(exc).casefold()


def _is_named_date_balance_query(text: str) -> bool:
    if re.search(r"\b(?:измени|обнови|установи|сверь)\b", text, re.IGNORECASE):
        return False
    return bool(_BALANCE_WORD_RE.search(text) and _NAMED_MONTH_RE.search(text))


async def handle_transaction_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    user_id: str,
    today: str,
    trace_ctx: "TraceContext",
    *,
    cfg: "Config",
    reader: "ExcelReader",
    llm_client: "LLMClient",
    pending: dict,
) -> None:
    try:
        await asyncio.wait_for(update.message.chat.send_action("typing"), timeout=3)
    except Exception:
        pass
    t0 = time.monotonic()
    try:
        txn = await parse_transaction(
            text=text,
            llm_client=llm_client,
            reader=reader,
            default_user=cfg.default_user,
            today=today,
            trace_ctx=trace_ctx,
        )
    except Exception as exc:
        logger.warning("parse_transaction failed: %s", type(exc).__name__)
        if _is_workbook_read_error(exc):
            await reply_to_update(
                update,
                context,
                "⚠️ Не удалось прочитать книгу бюджета. Администратору нужно восстановить "
                "валидную версию Excel и повторить синхронизацию.",
                trace_ctx=trace_ctx,
            )
            return
        await reply_to_update(
            update,
            context,
            "⚠️ Не получилось разобрать транзакцию: модель не ответила вовремя (таймаут/сеть). "
            "Попробуй ещё раз через пару секунд.",
            trace_ctx=trace_ctx,
        )
        return
    logger.info("parse_transaction took %.2fs", time.monotonic() - t0)

    if txn.confidence < cfg.confidence_threshold:
        msg = (
            f"Не уверен в разборе (уверенность: {txn.confidence:.0%}).\n\n"
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
    await reply_to_update(
        update, context, msg, parse_mode="HTML", reply_markup=keyboard, trace_ctx=trace_ctx,
    )


async def _run_agent(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    today: str,
    *,
    cfg: "Config",
    reader: "ExcelReader",
    llm_client: "LLMClient",
    trace_ctx: "TraceContext | None" = None,
) -> None:
    if trace_ctx is not None:
        trace_ctx.set_metadata("route", "budget_agent")
        trace_ctx.set_metadata("answer_source", "agent_tools")
    try:
        await asyncio.wait_for(update.message.chat.send_action("typing"), timeout=3)
    except Exception:
        pass
    try:
        answer = await run_budget_agent(
            question=text,
            reader=reader,
            llm_client=llm_client,
            tips=load_tips(),
            today=today,
            default_user=cfg.default_user,
            model=cfg.model,
            enable_code_tool=cfg.enable_unknown_code_executor,
            trace_ctx=trace_ctx,
            agent_config={
                "default_user": cfg.default_user,
                "salary_pay_days": list(cfg.salary_pay_days),
                "salary_payment_model": cfg.salary_payment_model,
                "salary_payment_percentages": list(cfg.salary_payment_percentages),
                "vacation_average_month_days": cfg.vacation_average_month_days,
                "vacation_ndfl_rate": cfg.vacation_ndfl_rate,
            },
        )
    except Exception as exc:
        logger.warning("run_budget_agent failed: %s", type(exc).__name__)
        answer = "⚠️ Не удалось получить ответ от модели (таймаут/сеть). Попробуй ещё раз."
    await reply_to_update(update, context, answer, parse_mode="HTML", trace_ctx=trace_ctx)


async def _run_deterministic_or_agent(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    today: str,
    *,
    cfg: "Config",
    reader: "ExcelReader",
    llm_client: "LLMClient",
    trace_ctx: "TraceContext | None" = None,
    unsupported_reply: str | None = None,
    unsupported_route: str = "deterministic_clarification",
) -> None:
    result = await answer_schema_guided_query(
        text,
        reader=reader,
        llm_client=llm_client,
        router_model=cfg.router_model,
        today=today,
        trace_ctx=trace_ctx,
    )
    if result.supported:
        if trace_ctx is not None:
            trace_ctx.set_metadata("route", "deterministic_query")
            trace_ctx.set_metadata("answer_source", "deterministic_query")
        await reply_to_update(
            update, context, result.answer, parse_mode="HTML", trace_ctx=trace_ctx,
        )
        return
    if unsupported_reply is not None:
        if trace_ctx is not None:
            trace_ctx.set_metadata("route", unsupported_route)
            trace_ctx.set_metadata("answer_source", "clarification")
        await reply_to_update(
            update, context, unsupported_reply, parse_mode="HTML", trace_ctx=trace_ctx,
        )
        return
    await _run_agent(
        update, context, text, today,
        cfg=cfg, reader=reader, llm_client=llm_client, trace_ctx=trace_ctx,
    )


async def dispatch(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    classification,
    text: str,
    user_id: str,
    today: str,
    trace_ctx: "TraceContext",
    *,
    cfg: "Config",
    reader: "ExcelReader",
    writer: "ExcelWriter",
    yadisk: "YadiskSync",
    llm_client: "LLMClient",
    pending: dict,
) -> None:
    intent = classification.intent
    extracted = classification.extracted_value
    logger.info("Intent: %s | text: %.60s", intent, text)

    if is_leave_pay_query(text):
        await _run_agent(
            update, context, text, today,
            cfg=cfg, reader=reader, llm_client=llm_client, trace_ctx=trace_ctx,
        )
    elif _is_named_date_balance_query(text) or is_expense_date_range_query(text):
        await _run_deterministic_or_agent(
            update, context, text, today,
            cfg=cfg, reader=reader, llm_client=llm_client, trace_ctx=trace_ctx,
        )
    elif intent == "transaction" and not has_transaction_amount(text):
        trace_ctx.set_metadata("route", "transaction_clarification")
        trace_ctx.set_metadata("answer_source", "clarification")
        await reply_to_update(
            update,
            context,
            "Чтобы добавить транзакцию, укажи сумму цифрами. "
            "Например: <i>билет на самолёт 5000</i>.",
            parse_mode="HTML",
            trace_ctx=trace_ctx,
        )
    elif intent == "transaction":
        trace_ctx.set_metadata("route", "transaction_parse")
        trace_ctx.set_metadata("answer_source", "transaction_parser")
        await handle_transaction_text(
            update, context, text, user_id, today, trace_ctx,
            cfg=cfg, reader=reader, llm_client=llm_client, pending=pending,
        )
    elif intent == "question":
        await _run_deterministic_or_agent(
            update, context, text, today,
            cfg=cfg, reader=reader, llm_client=llm_client, trace_ctx=trace_ctx,
        )
    elif intent == "optimization_advice":
        await _run_agent(update, context, text, today, cfg=cfg, reader=reader, llm_client=llm_client, trace_ctx=trace_ctx)
    elif intent == "salary_query":
        trace_ctx.set_metadata("route", "salary_query")
        await settings_cmd.cmd_salary_query(
            update, context, reader=reader, trace_ctx=trace_ctx,
        )
    elif intent == "salary_update":
        trace_ctx.set_metadata("route", "salary_update")
        await settings_cmd.cmd_salary_update(
            update, context, extracted_value=extracted or 0.0, text=text,
            reader=reader, writer=writer, yadisk=yadisk, cfg=cfg,
            trace_ctx=trace_ctx,
        )
    elif intent == "capital_query":
        if _DATE_REFERENCE_RE.search(text):
            await _run_deterministic_or_agent(
                update, context, text, today,
                cfg=cfg, reader=reader, llm_client=llm_client, trace_ctx=trace_ctx,
            )
        else:
            trace_ctx.set_metadata("route", "current_capital")
            trace_ctx.set_metadata("answer_source", "workbook")
            await settings_cmd.cmd_capital_query(
                update, context, reader=reader, trace_ctx=trace_ctx,
            )
    elif intent == "capital_update":
        trace_ctx.set_metadata("route", "capital_update")
        await settings_cmd.cmd_capital_update(
            update, context, extracted_value=extracted or 0.0,
            reader=reader, writer=writer, yadisk=yadisk,
            trace_ctx=trace_ctx,
        )
    elif intent == "payments_checklist":
        trace_ctx.set_metadata("route", "payments_checklist")
        await settings_cmd.cmd_payments_checklist(
            update,
            context,
            reader=reader,
            pay_days=cfg.salary_pay_days,
            payment_model=cfg.salary_payment_model,
            payment_percentages=cfg.salary_payment_percentages,
            trace_ctx=trace_ctx,
        )
    elif intent == "general_financial_advice":
        await _run_agent(update, context, text, today, cfg=cfg, reader=reader, llm_client=llm_client, trace_ctx=trace_ctx)
    elif intent == "off_topic":
        await reply_to_update(
            update, context,
            "Я бот для учёта личного бюджета и финансов. "
            "Могу помочь с транзакциями, аналитикой расходов, зарплатой, капиталом и инвестициями. "
            "Спроси что-нибудь по финансам 💰",
            trace_ctx=trace_ctx,
        )
    elif intent == "transaction_search":
        await _run_deterministic_or_agent(
            update, context, text, today,
            cfg=cfg, reader=reader, llm_client=llm_client, trace_ctx=trace_ctx,
            unsupported_reply=(
                "Укажи критерий поиска <b>в одном сообщении</b>. "
                "Например: <i>найди транзакцию: билет</i>."
            ),
            unsupported_route="transaction_search_clarification",
        )
    elif intent == "transaction_edit":
        trace_ctx.set_metadata("route", "transaction_edit")
        from handlers.edit import handle_transaction_edit
        await handle_transaction_edit(
            update, context, reader=reader, pending=pending, text=text,
            trace_ctx=trace_ctx,
        )
    elif intent == "unknown":
        await _run_deterministic_or_agent(
            update, context, text, today,
            cfg=cfg, reader=reader, llm_client=llm_client, trace_ctx=trace_ctx,
        )
    else:
        await _run_agent(
            update, context, text, today,
            cfg=cfg, reader=reader, llm_client=llm_client, trace_ctx=trace_ctx,
        )
