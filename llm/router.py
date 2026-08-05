from __future__ import annotations
import re
from typing import TYPE_CHECKING, Literal
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from llm.client import LLMClient
    from llm.tracing import TraceContext


class IntentClassification(BaseModel):
    intent: Literal[
        "transaction",
        "question",
        "salary_query",
        "salary_update",
        "capital_query",
        "capital_update",
        "payments_checklist",
        "optimization_advice",
        "general_financial_advice",
        "transaction_search",
        "transaction_edit",
        "off_topic",
        "unknown",
    ]
    extracted_value: float | None = Field(
        default=None,
        description="Numeric value for salary_update or capital_update",
    )


# Matches messages that look like transactions: contain a number and some text.
# Examples: "кофе 200", "200р кофе", "бензин 4500р", "получил зарплату 150000"
_TXN_RE = re.compile(
    r"(?:^|\s)(\d[\d\s]*(?:[.,]\d+)?)\s*(?:р(?:уб(?:лей?|ля?)?)?\.?|₽|k|к)?(?:\s|$)",
    re.IGNORECASE,
)
_EXPENSE_DATE_RANGE_RE = re.compile(
    r"(?=.*\b(?:трат\w*|потрат\w*|расход\w*)\b)(?=.*\bс\s+[0-3]?\d\s+по\s+[0-3]?\d\b)",
    re.IGNORECASE,
)
_LEAVE_PAY_RE = re.compile(
    r"\b(?:отпуск\w*|отпускн\w*)\b",
    re.IGNORECASE,
)


def is_expense_date_range_query(text: str) -> bool:
    """Return whether text is an expense analytics request with a day range."""
    return bool(_EXPENSE_DATE_RANGE_RE.search(text))


def is_leave_pay_query(text: str) -> bool:
    """Return whether a message asks about vacation/leave pay or its cash impact."""
    return bool(_LEAVE_PAY_RE.search(text))


def has_transaction_amount(text: str) -> bool:
    """Return whether text contains a numeric amount candidate."""
    return bool(_TXN_RE.search(text.strip()))


def _fast_classify(text: str) -> IntentClassification | None:
    """Return a transaction classification without LLM if the text clearly looks like one."""
    stripped = text.strip()
    if is_expense_date_range_query(stripped):
        return IntentClassification(intent="question", extracted_value=None)
    if is_leave_pay_query(stripped):
        return IntentClassification(intent="question", extracted_value=None)
    # Must have a number
    if not has_transaction_amount(stripped):
        return None
    # Must have at least some non-numeric, non-currency text (the description)
    description_part = _TXN_RE.sub("", stripped).strip()
    if len(description_part) < 2:
        return None
    low = stripped.lower()
    # Bail out on interrogative or command words — not plain transactions
    non_txn_words = (
        "сколько", "какой", "какая", "какие", "когда", "почему", "зачем", "как ",
        "что ", "измени", "обнови", "установи", "удали", "найди", "покажи",
        "зарплат", "капитал", "платеж", "платёж",
    )
    if any(w in low for w in non_txn_words):
        return None
    return IntentClassification(intent="transaction", extracted_value=None)


_SYSTEM_PROMPT = """You are an intent classifier for a Russian personal finance Telegram bot.

Classify the user's message into exactly one intent:

- transaction: Adding a new expense or income. Has an amount and description. Examples: "купил кофе 200р", "бензин 4500", "получил зарплату 100000"
- question: General question about spending, expenses, budget analytics — including requests that combine an aggregate ("сколько") with listing ("покажи транзакции"). Examples: "сколько потратил на еду в апреле?", "какие были траты за март?", "сколько потратил в буханке за последний месяц и покажи транзакции", "покажи расходы за май с итогом"
- salary_query: Asking what the salary amount is. Examples: "какая у меня зарплата?", "сколько зарплата?"
- salary_update: Changing a salary value. Extract the new amount into extracted_value. Examples: "измени зарплату на 80000", "установи зарплату 120000 рублей"
- capital_query: Asking about current total capital/savings. Examples: "сколько у меня денег?", "какой у меня капитал?"
- capital_update: Changing the capital value. Extract the new amount into extracted_value. Examples: "измени капитал на 700000", "установи капитал 500000 рублей"
- payments_checklist: Requesting a list of mandatory upcoming payments. Examples: "покажи обязательные платежи", "что нужно заплатить?", "чеклист платежей"
- optimization_advice: Asking for advice on saving money or optimizing personal spending. Examples: "как снизить траты?", "что можно оптимизировать в расходах?"
- general_financial_advice: General questions about personal finance, investments, budgeting strategies, earning more — not requiring data from the user's spreadsheet. Examples: "как зарабатывать больше?", "куда вложить деньги?", "что такое ETF?", "как правильно инвестировать?", "стоит ли брать ипотеку?", "как сформировать подушку безопасности?"
- transaction_search: Looking up or finding a specific past transaction by description or date — pure lookup, no aggregate amount requested. Examples: "найди транзакцию на кофе", "была ли трата на кафе Буханка?", "найди платёж от 5 мая"
- transaction_edit: Wanting to directly change or delete a specific existing transaction. Examples: "измени транзакцию", "удали последний платёж", "исправь сумму". NOT for questions or analysis about recent transactions.
- off_topic: Anything not related to personal finance, money, budget, investments, or the user's financial data. Examples: "расскажи анекдот", "как приготовить борщ?", "кто выиграл чемпионат мира?", "напиши стихотворение"
- unknown: Financial or data-related request that doesn't fit any of the above.

For salary_update and capital_update, extract the numeric value into extracted_value (rubles, plain number).
"""


async def classify_intent(
    text: str,
    llm_client: "LLMClient",
    router_model: str,
    trace_ctx: "TraceContext | None" = None,
) -> IntentClassification:
    fast = _fast_classify(text)
    if fast is not None:
        return fast
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]
    return await llm_client.chat_structured(
        messages=messages,
        response_model=IntentClassification,
        model_override=router_model,
        trace_ctx=trace_ctx,
        span_name="classify_intent",
    )
