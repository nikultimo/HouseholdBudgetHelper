from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from telegram_helpers import reply_to_update

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes
    from llm.client import LLMClient
    from excel.reader import ExcelReader
    from config import Config

logger = logging.getLogger(__name__)

_SEARCH_SYSTEM_PROMPT = """You are a search assistant for a Russian personal budget app.
Parse the user's search request and extract structured search criteria.
Return null for fields not mentioned.
For month, use YYYY-MM format (e.g. "2025-04"). If the user says "в апреле" without a year, use the current year."""


class SearchQuery(BaseModel):
    category: str | None = Field(None, description="Category name or partial match (Russian)")
    description_contains: str | None = Field(None, description="Text to search in description (Russian)")
    month: str | None = Field(None, description="Month in YYYY-MM format, or null for all time")
    limit: int = Field(20, ge=1, le=50)


def _filter_transactions(txns: list[dict[str, Any]], query: SearchQuery) -> list[dict[str, Any]]:
    results = txns
    if query.month:
        results = [t for t in results if t.get("month") == query.month]
    if query.category:
        q = query.category.lower()
        results = [t for t in results if q in t.get("category", "").lower()]
    if query.description_contains:
        q = query.description_contains.lower()
        results = [t for t in results if q in t.get("description", "").lower()]
    results = sorted(results, key=lambda t: (t.get("date"), t.get("row", 0)), reverse=True)
    return results[:query.limit]


def _format_search_results(results: list[dict[str, Any]], query: SearchQuery) -> str:
    if not results:
        return "Ничего не найдено по вашему запросу."

    lines = [f"<b>🔍 Найдено: {len(results)}</b>"]
    total = sum(t["amount"] for t in results if t.get("type") == "Расход")
    if total:
        lines.append(f"Итого расходов: <b>{total:,.0f} ₽</b>")
    lines.append("")
    for t in results[:20]:
        date_str = t["date"].strftime("%d.%m.%Y") if hasattr(t["date"], "strftime") else str(t["date"])
        sign = "💸" if t.get("type") == "Расход" else "💰"
        lines.append(
            f"{sign} <b>{t['amount']:,.0f} ₽</b> — <i>{t['description']}</i> "
            f"[{t['category']}] {date_str}"
        )
    if len(results) > 20:
        lines.append(f"...и ещё {len(results) - 20}")
    return "\n".join(lines)


async def handle_transaction_search(
    text: str,
    update: "Update",
    context: "ContextTypes.DEFAULT_TYPE",
    reader: "ExcelReader",
    llm_client: "LLMClient",
    cfg: "Config",
    today: str,
) -> None:
    try:
        messages = [
            {"role": "system", "content": _SEARCH_SYSTEM_PROMPT + f"\nToday: {today}"},
            {"role": "user", "content": text},
        ]
        query: SearchQuery = await llm_client.chat_structured(
            messages=messages,
            response_model=SearchQuery,
            model_override=cfg.router_model,
        )
    except Exception as exc:
        logger.warning("search query parse failed: %s", exc)
        query = SearchQuery()

    txns = reader.get_transactions()
    results = _filter_transactions(txns, query)
    reply = _format_search_results(results, query)
    await reply_to_update(update, context, reply, parse_mode="HTML")
