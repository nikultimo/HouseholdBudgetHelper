from __future__ import annotations
import re
from datetime import date
from typing import Literal
from pydantic import BaseModel, Field

from llm.client import LLMClient
from llm.ru_months import find_month


class TransactionFilters(BaseModel):
    month: str | None = Field(None, description="Month in YYYY-MM format.")
    date_from: str | None = Field(None, description="Start date in YYYY-MM-DD format.")
    date_to: str | None = Field(None, description="End date in YYYY-MM-DD format.")
    category: str | None = None
    type: Literal["Расход", "Доход"] | None = None
    whose: str | None = None
    account: str | None = None
    mandatory: Literal["Да", "Нет"] | None = None
    description_contains: str | None = None
    amount_min: float | None = None
    amount_max: float | None = None


class BudgetQueryPlan(BaseModel):
    operation: Literal[
        "aggregate_transactions",
        "list_transactions",
        "category_breakdown",
        "monthly_trend",
        "compare_periods",
        "balance_at_date",
        "projected_capital",
        "expense_period_summary",
        "settings_lookup",
        "payments_lookup",
        "credits_lookup",
        "unsupported",
    ] = Field(description="The deterministic query operation to execute.")
    filters: TransactionFilters = Field(default_factory=TransactionFilters)
    compare_filters: TransactionFilters | None = Field(
        None,
        description="Second period/filter set for compare_periods.",
    )
    aggregation: Literal["sum", "count", "average", "min", "max"] | None = None
    group_by: Literal["category", "month", "type", "whose", "account", "mandatory"] | None = None
    lookup: Literal["salary", "capital", "categories", "accounts", "settings"] | None = None
    payments_half: Literal["first", "second", "all"] | None = None
    limit: int = Field(20, ge=1, le=50)
    as_of_date: str | None = Field(None, description="Date for a start-of-day balance in YYYY-MM-DD.")
    reason: str = Field(
        "",
        description="Brief reason for the plan or why the request is unsupported.",
    )


_QUERY_PLANNER_PROMPT = """You plan safe deterministic queries for a Russian personal budget bot.

Return a BudgetQueryPlan only. Do not calculate the answer.

Use these operations:
- aggregate_transactions: exact sum/count/average/min/max over transactions.
- list_transactions: list matching transactions.
- category_breakdown: expense totals grouped by category.
- monthly_trend: totals grouped by month.
- compare_periods: compare two transaction filter sets.
- balance_at_date: aggregate RUB capital at the start of a specific date.
- projected_capital: projected RUB capital at the start of a future date.
- expense_period_summary: expense total, count, category breakdown, and transaction list.
- settings_lookup: salary, capital, categories, accounts, or settings.
- payments_lookup: mandatory payments.
- credits_lookup: credit/debt summaries.
- unsupported: advice, vague questions, external-data requests, mutation requests, or anything not expressible safely.

Rules:
- Use YYYY-MM for month and YYYY-MM-DD for dates.
- If the user says "this/current month", use today's month.
- If the user names a month without a year, use today's year.
- For spending/expenses, set type to "Расход". For income, set type to "Доход".
- Prefer category only when the user asks for a category; use description_contains for merchant/item text.
- For broad advice or optimization, return unsupported so the analysis handler can answer.
"""

def _mentioned_year(text: str, default: int) -> int:
    match = re.search(r"\b(20\d{2})\b", text)
    return int(match.group(1)) if match else default


def _fast_plan(text: str, today: str) -> BudgetQueryPlan | None:
    """Deterministic coverage for common Russian period and balance queries."""
    low = text.casefold().strip()
    if re.search(r"\b(?:отпуск\w*|отпускн\w*)\b", low):
        return BudgetQueryPlan(
            operation="unsupported",
            reason="Vacation and leave-pay questions require the typed leave estimate tool.",
        )
    today_date = date.fromisoformat(today)
    month_match = find_month(low)
    month = month_match[0] if month_match else None
    year = _mentioned_year(low, today_date.year)

    range_match = re.search(r"\bс\s+([0-3]?\d)\s+по\s+([0-3]?\d)", low)
    is_expense_period = bool(re.search(r"\b(?:трат\w*|расход\w*)\b", low))
    is_expense_range = bool(
        range_match and re.search(r"\b(?:трат\w*|потрат\w*|расход\w*)\b", low)
    )
    if month is not None and (is_expense_period or is_expense_range):
        if range_match:
            try:
                start = date(year, month, int(range_match.group(1)))
                end = date(year, month, int(range_match.group(2)))
            except ValueError:
                return None
            filters = TransactionFilters(
                date_from=start.isoformat(), date_to=end.isoformat(), type="Расход",
            )
        else:
            filters = TransactionFilters(month=f"{year}-{month:02d}", type="Расход")
        return BudgetQueryPlan(operation="expense_period_summary", filters=filters, limit=20)

    is_balance = bool(re.search(r"\b(денег|баланс|капитал|остаток)\b", low))
    if is_balance and month:
        # Only accept a day number immediately preceding the matched month word
        # (e.g. "15 июня", "15-го июня"), not any stray number in the message.
        window = re.sub(r"\b20\d{2}\b", "", low[: month_match[1]])
        day_match = re.search(r"(?<!\d)([0-3]?\d)(?:-?(?:го|ого|е))?\s*$", window)
        day = int(day_match.group(1)) if day_match else 1
        try:
            target = date(year, month, day)
        except ValueError:
            return None
        operation = "projected_capital" if target > today_date else "balance_at_date"
        return BudgetQueryPlan(operation=operation, as_of_date=target.isoformat())
    return None


async def plan_budget_query(
    text: str,
    llm_client: LLMClient,
    router_model: str,
    today: str,
    trace_ctx=None,
) -> BudgetQueryPlan:
    fast = _fast_plan(text, today)
    if fast is not None:
        return fast
    messages = [
        {"role": "system", "content": f"{_QUERY_PLANNER_PROMPT}\nToday: {today}"},
        {"role": "user", "content": text},
    ]
    return await llm_client.chat_structured(
        messages=messages,
        response_model=BudgetQueryPlan,
        model_override=router_model,
        trace_ctx=trace_ctx,
        span_name="plan_budget_query",
    )
