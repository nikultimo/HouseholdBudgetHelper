"""Tool schemas and executor functions for the budget agent.

All tools are read-only. Executors reuse filtering/aggregation helpers from
handlers/query.py to avoid duplicating logic.
"""
from __future__ import annotations

import asyncio
import builtins as _builtins_mod
import json
import logging
from typing import TYPE_CHECKING, Any

from datetime import date

from handlers.query import (
    _aggregate,
    _filter_transactions,
    _format_txn,
    _money,
    _period_label,
)
from llm.code_executor import _READER_API_DOC, _SAFE_BUILTINS_NAMES, _guard
from salary.calculator import calculate_payment
from salary.calendar_parser import fetch_calendar, public_holidays_in_period, working_days_in_period

if TYPE_CHECKING:
    from excel.reader import ExcelReader

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared filter parameter schema (reused in several tools)
# ---------------------------------------------------------------------------

_FILTER_PROPS: dict[str, Any] = {
    "month": {"type": "string", "description": "Month in YYYY-MM format."},
    "date_from": {"type": "string", "description": "Start date YYYY-MM-DD (inclusive)."},
    "date_to": {"type": "string", "description": "End date YYYY-MM-DD (inclusive)."},
    "category": {"type": "string", "description": "Category substring (case-insensitive)."},
    "type": {"type": "string", "enum": ["Расход", "Доход"], "description": "Transaction type."},
    "whose": {
        "type": "string",
        "description": (
            "Name of the household member who PAID/SPENT. "
            "Use ONLY when filtering by WHO in the household spent the money. "
            "NEVER use for recipients, merchants, or stores — use description_contains for those. "
            "Example: 'сколько <плательщик> потратил' → whose='<плательщик>'. "
            "'сколько отдал <получателю>' → description_contains='<получатель>' (получатель — не плательщик)."
        ),
    },
    "account": {"type": "string", "description": "Account/card name substring (e.g. 'Tinkoff')."},
    "mandatory": {"type": "string", "enum": ["Да", "Нет"], "description": "Mandatory flag."},
    "description_contains": {
        "type": "string",
        "description": (
            "Merchant name, store, café, place, or item text — WHERE money was spent or WHAT was bought "
            "(e.g. 'буханка', 'кофе', 'пятёрочка', 'яндекс'). Case-insensitive substring match."
        ),
    },
    "amount_min": {"type": "number"},
    "amount_max": {"type": "number"},
}


# ---------------------------------------------------------------------------
# Tool definitions (sorted by name for deterministic prompt caching)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "analyze_cashflow",
            "description": (
                "Analyze where money went and what the observed income-minus-expense surplus was. "
                "Use for compound questions about spending categories, missing money, or realistic "
                "saving capacity. The result explicitly distinguishes observed cash flow from a "
                "guaranteed savings promise."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "months": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional calendar months in YYYY-MM form.",
                    },
                    "trailing_months": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 12,
                        "description": "Number of latest available months when months is omitted. Default 3.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "aggregate_transactions",
            "description": (
                "Calculate a single numeric aggregate (sum, count, average, min, max) "
                "over transactions matching the given filters."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    **_FILTER_PROPS,
                    "aggregation": {
                        "type": "string",
                        "enum": ["sum", "count", "average", "min", "max"],
                        "description": "Aggregation function. Defaults to sum.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "estimate_leave_impact",
            "description": (
                "Return a transparent proxy estimate of vacation pay and adjusted salary payments "
                "for paid vacation plus optional unpaid leave. It uses the configured household "
                "member's current net monthly salary, not full payroll history, and labels all "
                "assumptions. "
                "NOTE: this tool only covers salary adjustments — it does NOT return the user's "
                "total balance or future capital. To answer 'how much money will I have by month X', "
                "you MUST also call get_capital to obtain the starting balance and projected monthly flow."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "vacation_start": {
                        "type": "string",
                        "description": "Vacation start date YYYY-MM-DD (inclusive).",
                    },
                    "vacation_end": {
                        "type": "string",
                        "description": "Vacation end date YYYY-MM-DD (inclusive).",
                    },
                    "member": {
                        "type": "string",
                        "description": "Household member whose salary should be used. Defaults to configured user.",
                    },
                    "unpaid_leave_start": {
                        "type": "string",
                        "description": "Optional unpaid leave start date YYYY-MM-DD inclusive.",
                    },
                    "unpaid_leave_end": {
                        "type": "string",
                        "description": "Optional unpaid leave end date YYYY-MM-DD inclusive.",
                    },
                },
                "required": ["vacation_start", "vacation_end"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "category_breakdown",
            "description": (
                "Return expense totals grouped by category for the given filters. "
                "Defaults to type=Расход when type is not specified."
            ),
            "parameters": {
                "type": "object",
                "properties": {**_FILTER_PROPS},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_capital",
            "description": (
                "Return capital balance and projected monthly salary inflows for all months. "
                "Use this to answer questions about total savings or future balance. "
                "For vacation+projection queries ('how much will I have by August if I take vacation'), "
                "call this together with estimate_leave_impact: combine the starting balance from "
                "get_capital with the vacation-adjusted payment amounts from estimate_leave_impact "
                "to compute the projected balance at the target month."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_credits",
            "description": "Return credit/loan summary: balances, monthly payments, rates.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_mandatory_payments",
            "description": "Return mandatory payment list split by salary half (first/second).",
            "parameters": {
                "type": "object",
                "properties": {
                    "half": {
                        "type": "string",
                        "enum": ["first", "second", "all"],
                        "description": "Which salary half to show. Defaults to all.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_salary",
            "description": "Return salary settings (Зарплата keys and their values).",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "monthly_trend",
            "description": "Return transaction totals grouped by month for the given filters.",
            "parameters": {
                "type": "object",
                "properties": {**_FILTER_PROPS},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_transactions",
            "description": (
                "Search and list individual transactions matching the given filters, "
                "sorted by date descending."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    **_FILTER_PROPS,
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "description": "Maximum number of transactions to return. Defaults to 20.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
]

_CODE_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "run_python_code",
        "description": (
            "Execute a custom Python snippet against the budget reader to answer complex "
            "questions that predefined tools cannot cover. "
            f"Available reader API:\n{_READER_API_DOC}\n"
            f"Allowed builtins: {', '.join(_SAFE_BUILTINS_NAMES)}. "
            "Store the final answer in a variable called `result`. "
            "Do NOT use import, open, exec, eval, or __dunder__ names."
        ),
        "parameters": {
            "type": "object",
            "required": ["code", "explanation"],
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python snippet using `reader`. Store result in `result`.",
                },
                "explanation": {
                    "type": "string",
                    "description": "One sentence: what this code computes.",
                },
            },
            "additionalProperties": False,
        },
    },
}


def get_tool_schemas(enable_code_tool: bool = False) -> list[dict[str, Any]]:
    """Return sorted tool list; optionally include the code-execution tool."""
    schemas = list(TOOL_SCHEMAS)
    if enable_code_tool:
        schemas.append(_CODE_TOOL_SCHEMA)
    return schemas


# ---------------------------------------------------------------------------
# Argument parsing helpers
# ---------------------------------------------------------------------------

def _morpho_stem(word: str) -> str | None:
    """Return a shorter Russian stem by stripping up to 2 trailing chars.

    Covers the most common inflected endings so substring search catches all
    grammatical forms of a name or noun:
      Маше/Машу/Маши → Маш   (3 chars — matches Маша, Маше, Машу, Маши)
      буханке/буханку → бухан (5 chars — still a unique substring)
    Returns None if the word is already ≤3 chars (nothing useful to strip).
    """
    w = word.strip()
    stem_len = max(3, len(w) - 2)
    stem = w[:stem_len]
    return stem if stem != w else None


def _parse_args(raw: str | dict) -> dict:
    if isinstance(raw, str):
        return json.loads(raw)
    return dict(raw)


def _build_filters(args: dict):
    from llm.query_planner import TransactionFilters

    return TransactionFilters(
        month=args.get("month"),
        date_from=args.get("date_from"),
        date_to=args.get("date_to"),
        category=args.get("category"),
        type=args.get("type"),
        whose=args.get("whose"),
        account=args.get("account"),
        mandatory=args.get("mandatory"),
        description_contains=args.get("description_contains"),
        amount_min=args.get("amount_min"),
        amount_max=args.get("amount_max"),
    )


# ---------------------------------------------------------------------------
# Executors (sync — called inside asyncio.to_thread)
# ---------------------------------------------------------------------------

def _apply_morpho_fallback(args: dict, all_txns: list) -> tuple[list, str]:
    """Retry with stems/split words if exact filter returned no results.

    Pass 1 — stem: strip last 2 chars of description_contains/whose to match
    inflected Russian forms (Маше→Маш, буханке→бухан).

    Pass 2 — split: if description_contains is multi-word (e.g. "Катя английский")
    and pass 1 also failed, try each word as a separate description_contains filter
    and return the union, so "Катя английский" matches "Английский (Катя)".
    """
    # Pass 1: stem
    stem_args = dict(args)
    stemmed_fields: list[str] = []
    for field in ("description_contains", "whose"):
        val = args.get(field)
        if val:
            stem = _morpho_stem(val)
            if stem:
                stem_args[field] = stem
                stemmed_fields.append(f"{field}='{stem}'")
    if stemmed_fields:
        txns = _filter_transactions(all_txns, _build_filters(stem_args))
        if txns:
            return txns, f" (поиск по корню: {', '.join(stemmed_fields)})"

    # Pass 2: split multi-word description_contains
    desc = args.get("description_contains", "")
    words = [w.strip() for w in desc.split() if len(w.strip()) >= 3] if desc else []
    if len(words) >= 2:
        seen: set[int] = set()
        merged: list = []
        for word in words:
            word_args = {**args, "description_contains": word}
            for t in _filter_transactions(all_txns, _build_filters(word_args)):
                row = t.get("row", id(t))
                if row not in seen:
                    seen.add(row)
                    merged.append(t)
        if merged:
            return merged, f" (поиск по словам: {', '.join(words)})"

    return [], ""


def _exec_search_transactions(args: dict, reader: "ExcelReader") -> str:
    filters = _build_filters(args)
    limit = max(1, min(50, int(args.get("limit") or 20)))
    all_txns = reader.get_transactions()
    txns = _filter_transactions(all_txns, filters)

    stem_note = ""
    if not txns:
        txns, stem_note = _apply_morpho_fallback(args, all_txns)

    results = sorted(txns, key=lambda t: (t.get("date"), t.get("row", 0)), reverse=True)[:limit]
    if not results:
        return "Ничего не найдено."
    label = _period_label(filters)
    total = sum(float(t.get("amount") or 0) for t in results if t.get("type") == "Расход")
    lines = [f"Найдено: {len(results)}, период: {label}{stem_note}"]
    if total:
        lines.append(f"Итого расходов в списке: {_money(total)}")
    lines.extend(_format_txn(t) for t in results)
    return "\n".join(lines)


def _exec_aggregate_transactions(args: dict, reader: "ExcelReader") -> str:
    filters = _build_filters(args)
    aggregation = str(args.get("aggregation") or "sum")
    all_txns = reader.get_transactions()
    txns = _filter_transactions(all_txns, filters)

    stem_note = ""
    if not txns:
        txns, stem_note = _apply_morpho_fallback(args, all_txns)

    value = _aggregate(txns, aggregation)
    label = _period_label(filters)
    if aggregation == "count":
        return f"Транзакций за {label}: {int(value)}{stem_note}"
    return f"Итого ({aggregation}) за {label}: {_money(value)}{stem_note}"


def _exec_category_breakdown(args: dict, reader: "ExcelReader") -> str:
    filters = _build_filters(args)
    txns = _filter_transactions(reader.get_transactions(), filters)
    if filters.type is None:
        txns = [t for t in txns if t.get("type") == "Расход"]
    label = _period_label(filters)
    totals: dict[str, float] = {}
    for t in txns:
        key = str(t.get("category") or "Без категории")
        totals[key] = totals.get(key, 0.0) + float(t.get("amount") or 0)
    if not totals:
        return "Нет данных."
    items = sorted(totals.items(), key=lambda x: x[1], reverse=True)
    lines = [f"Расходы по категориям ({label}):"]
    lines.extend(f"{name}: {_money(total)}" for name, total in items)
    return "\n".join(lines)


def _exec_monthly_trend(args: dict, reader: "ExcelReader") -> str:
    filters = _build_filters(args)
    txns = _filter_transactions(reader.get_transactions(), filters)
    totals: dict[str, float] = {}
    for t in txns:
        key = str(t.get("month") or "?")
        totals[key] = totals.get(key, 0.0) + float(t.get("amount") or 0)
    if not totals:
        return "Нет данных."
    items = sorted(totals.items())
    lines = ["Динамика по месяцам:"]
    lines.extend(f"{month}: {_money(total)}" for month, total in items)
    return "\n".join(lines)


def _exec_analyze_cashflow(args: dict, reader: "ExcelReader") -> str:
    transactions = reader.get_transactions()
    requested = [str(month) for month in args.get("months") or []]
    if requested:
        months = requested
    else:
        available = sorted({str(txn.get("month") or "") for txn in transactions if txn.get("month")})
        trailing = max(1, min(12, int(args.get("trailing_months") or 3)))
        months = available[-trailing:]
    if not months:
        return "Недостаточно транзакций для анализа денежного потока."

    selected = [txn for txn in transactions if str(txn.get("month") or "") in months]
    income = sum(float(txn.get("amount") or 0) for txn in selected if txn.get("type") == "Доход")
    expenses = sum(float(txn.get("amount") or 0) for txn in selected if txn.get("type") == "Расход")
    observed = income - expenses
    categories: dict[str, float] = {}
    for txn in selected:
        if txn.get("type") != "Расход":
            continue
        category = str(txn.get("category") or "Без категории")
        categories[category] = categories.get(category, 0.0) + float(txn.get("amount") or 0)

    lines = [
        f"Периоды: {', '.join(months)}",
        f"Доходы по подтверждённым транзакциям: {_money(income)}",
        f"Расходы по подтверждённым транзакциям: {_money(expenses)}",
        f"Наблюдаемый денежный поток (доходы − расходы): {_money(observed)}",
        "Это исторический остаток потока, а не гарантированная сумма накоплений.",
        "Расходы по категориям:",
    ]
    lines.extend(
        f"  {category}: {_money(total)}"
        for category, total in sorted(categories.items(), key=lambda item: item[1], reverse=True)
    )
    return "\n".join(lines)


def _exec_get_capital(reader: "ExcelReader") -> str:
    from datetime import datetime as _dt
    data = reader.get_capital_data()
    if not data:
        return "Данные по капиталу не найдены."

    payments = reader.get_mandatory_payments()
    mandatory_total = sum(float(p.get("amount") or 0) for p in payments.get("first", []))
    mandatory_total += sum(float(p.get("amount") or 0) for p in payments.get("second", []))

    current_month = _dt.now().strftime("%Y-%m")
    # Build list of current + 12 forward months (same window as cmd_capital_query)
    now = _dt.now()
    display_months = []
    y, m = now.year, now.month
    for _ in range(13):
        display_months.append(f"{y}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1

    lines = ["Капитал по месяцам (прогноз):"]
    projected_balance: float | None = None

    for month in display_months:
        if month not in data:
            continue
        row = data[month]
        rubles = row.get("rubles") or 0
        usd = row.get("usd") or 0
        investments = row.get("investments") or 0
        crypto = row.get("crypto") or 0
        debts = row.get("debts") or 0

        if month == current_month:
            net = rubles + usd + investments + crypto - debts
            projected_balance = net
            lines.append(f"▶ {month} (текущий): чистый баланс {_money(net)}")
        else:
            sal_first = row.get("salary_first") or 0
            sal_second = row.get("salary_second") or 0
            if projected_balance is None or (sal_first == 0 and sal_second == 0):
                continue
            projected_balance = projected_balance + sal_first + sal_second - mandatory_total
            lines.append(
                f"  {month}: прогноз {_money(projected_balance)}"
                f" (+{_money(sal_first + sal_second)} зп"
                + (f" −{_money(mandatory_total)} платежи" if mandatory_total else "")
                + ")"
            )

    return "\n".join(lines)


def _exec_calculate_vacation_pay(args: dict, reader: "ExcelReader", config: dict) -> str:
    try:
        start = date.fromisoformat(str(args.get("vacation_start", "")))
        end   = date.fromisoformat(str(args.get("vacation_end", "")))
    except ValueError as exc:
        return f"[error] Неверный формат даты: {exc}"
    if end < start:
        return "[error] vacation_end должна быть >= vacation_start"

    settings = reader.get_settings()
    salary_items = {k: float(v or 0) for k, v in settings.items() if str(k).startswith("Зарплата ")}
    if not salary_items:
        return "Зарплата не указана в настройках."
    requested_member = str(args.get("member") or config.get("default_user") or "").strip()
    selected_key = ""
    if requested_member:
        selected_key = next(
            (
                key for key in salary_items
                if key.removeprefix("Зарплата ").casefold() == requested_member.casefold()
            ),
            "",
        )
    if not selected_key and len(salary_items) == 1:
        selected_key = next(iter(salary_items))
    if not selected_key:
        return (
            "[clarification] Уточни участника домохозяйства для оценки отпускных; "
            "в настройках найдено несколько зарплат."
        )
    member = selected_key.removeprefix("Зарплата ")
    monthly_salary = salary_items[selected_key]
    if monthly_salary <= 0:
        return f"[clarification] Для участника {member} не указана положительная зарплата."

    calendar_days = (end - start).days + 1
    ndfl = float(config.get("vacation_ndfl_rate", 0.13))
    average_month_days = float(config.get("vacation_average_month_days", 29.3))
    if not 0 <= ndfl < 1:
        return "[error] vacation_ndfl_rate должна быть от 0 до 1"
    if average_month_days <= 0:
        return "[error] vacation_average_month_days должна быть положительной"

    # Find public holidays inside the vacation range (ТК РФ ст. 120: не включаются в счёт)
    cal_vac = fetch_calendar(start.year)
    if end.year != start.year:
        cal_vac = {**cal_vac, **fetch_calendar(end.year)}
    holidays_in_vac = public_holidays_in_period(start, end, cal_vac)
    vacation_days = calendar_days - len(holidays_in_vac)

    # Salary settings are net amounts throughout the salary calculator. Calculate
    # the net proxy directly, then derive an illustrative gross value once.
    avg_daily_net = monthly_salary / average_month_days
    vacation_pay_net = round(avg_daily_net * vacation_days, 2)
    vacation_pay_gross = round(vacation_pay_net / (1 - ndfl), 2)

    holiday_note = ""
    if holidays_in_vac:
        dates_str = ", ".join(d.strftime("%d.%m") for d in sorted(holidays_in_vac))
        holiday_note = f", из них праздников: {len(holidays_in_vac)} ({dates_str})"

    lines = [
        f"Участник: {member}",
        "Тип расчёта: прокси-оценка по текущей чистой месячной зарплате; не расчёт работодателя.",
        f"Отпуск: {start.strftime('%d.%m.%Y')} — {end.strftime('%d.%m.%Y')} ({calendar_days} кал. дн.{holiday_note})",
        f"Оплачиваемых дней отпуска: {vacation_days}",
        f"Среднедневная чистая ставка: {avg_daily_net:,.0f} руб  "
        f"(чистая зарплата {monthly_salary:,.0f} / {average_month_days:g})",
        f"Отпускные чистыми (оценка): {vacation_pay_net:,.0f} руб  "
        f"({avg_daily_net:,.0f} × {vacation_days})",
        f"Эквивалент до НДФЛ ~{int(ndfl * 100)}%: {vacation_pay_gross:,.0f} руб",
        "",
    ]

    raw_unpaid_periods = list(args.get("unpaid_leave_periods") or [])
    if args.get("unpaid_leave_start") or args.get("unpaid_leave_end"):
        raw_unpaid_periods.append({
            "start": args.get("unpaid_leave_start"),
            "end": args.get("unpaid_leave_end"),
        })
    unpaid_periods: list[tuple[date, date]] = []
    for item in raw_unpaid_periods:
        try:
            unpaid_start = date.fromisoformat(str(item.get("start", "")))
            unpaid_end = date.fromisoformat(str(item.get("end", "")))
        except (AttributeError, ValueError) as exc:
            return f"[error] Неверный период отпуска за свой счёт: {exc}"
        if unpaid_end < unpaid_start:
            return "[error] Конец отпуска за свой счёт должен быть не раньше начала"
        unpaid_periods.append((unpaid_start, unpaid_end))
    if unpaid_periods:
        lines.append(
            "Отпуск за свой счёт: "
            + ", ".join(
                f"{period_start.strftime('%d.%m.%Y')}–{period_end.strftime('%d.%m.%Y')}"
                for period_start, period_end in unpaid_periods
            )
        )
        lines.append("")

    pay_days: tuple[int, int] = tuple(config.get("salary_pay_days", [5, 20]))  # type: ignore[assignment]
    payment_model: str = config.get("salary_payment_model", "working_days")
    payment_percentages: tuple[float, float] = tuple(config.get("salary_payment_percentages", [0.5, 0.5]))  # type: ignore[assignment]
    mandatory_payments = reader.get_mandatory_payments()
    all_mandatory = mandatory_payments.get("first", []) + mandatory_payments.get("second", [])

    # Collect calendar months spanned by paid/unpaid leave, plus one extra forward month
    # (the 5th pays for the second half of the previous month, so a late-month
    # vacation can push into the next month's 5th pay date).
    affected_months: set[tuple[int, int]] = set()
    for period_start, period_end in [(start, end), *unpaid_periods]:
        cur = date(period_start.year, period_start.month, 1)
        end_month = date(period_end.year, period_end.month, 1)
        while cur <= end_month:
            affected_months.add((cur.year, cur.month))
            cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    for y, m in list(affected_months):
        ny, nm = (y, m + 1) if m < 12 else (y + 1, 1)
        affected_months.add((ny, nm))

    lines.append("Выплаты, затронутые отпуском:")
    any_affected = False
    for y, m in sorted(affected_months):
        for pay_day in sorted(pay_days):
            try:
                pd = date(y, m, pay_day)
            except ValueError:
                continue
            try:
                base = calculate_payment(
                    pd, monthly_salary, all_mandatory,
                    ndfl_rate=ndfl, pay_days=pay_days,
                    payment_model=payment_model,
                    payment_percentages=payment_percentages,
                )
            except (ValueError, Exception):
                continue

            leave_periods = [(start, end), *unpaid_periods]
            overlaps = [
                (max(period_start, base.period_start), min(period_end, base.period_end))
                for period_start, period_end in leave_periods
            ]
            overlaps = [period for period in overlaps if period[0] <= period[1]]
            if not overlaps:
                continue

            period_str = f"{base.period_start.strftime('%d.%m')}–{base.period_end.strftime('%d.%m')}"
            if payment_model == "working_days":
                cal = fetch_calendar(base.period_start.year)
                if base.period_end.year != base.period_start.year:
                    cal = {**cal, **fetch_calendar(base.period_end.year)}
                vac_working = sorted({
                    work_day
                    for overlap_start, overlap_end in overlaps
                    for work_day in working_days_in_period(overlap_start, overlap_end, cal)
                })
                vac_dates = ", ".join(d.strftime("%d.%m") for d in sorted(vac_working))
                adjusted_days = max(0, base.working_days_period - len(vac_working))
                if base.working_days_month:
                    daily = monthly_salary / base.working_days_month
                    adj_net = round(daily * adjusted_days, 2)
                else:
                    adj_net = base.net_amount
                lines.append(
                    f"  Выплата {pd.strftime('%d.%m.%Y')} (период {period_str}): "
                    f"{adj_net:,.0f} руб вместо {base.net_amount:,.0f} руб. "
                    f"Рабочих дней в периоде: {base.working_days_period}, "
                    f"из них в отпуске: {len(vac_working)} ({vac_dates if vac_dates else 'нет'}), "
                    f"оплачивается: {adjusted_days} раб. дн."
                )
            else:
                lines.append(
                    f"  Выплата {pd.strftime('%d.%m.%Y')} (период {period_str}): "
                    f"{base.net_amount:,.0f} руб по плановой фиксированной доле; "
                    "фактическое уменьшение из-за отпуска эта модель не оценивает."
                )
            any_affected = True

    if not any_affected:
        lines.append("  Ни одна плановая выплата не затронута отпуском.")

    lines.append("")
    lines.append(
        "Ограничения: не учтены выплаты и исключаемые периоды за предыдущие 12 месяцев, "
        "премии, больничные и правила работодателя."
    )
    lines.append("Примечание: срок фактической выплаты определяет работодатель.")
    lines.append("")
    lines.append(
        "Подсказка: для прогноза общего капитала к конкретному месяцу с учётом "
        "изменённых выплат — также вызови get_capital()."
    )
    return "\n".join(lines)


def _exec_get_salary(reader: "ExcelReader") -> str:
    settings = reader.get_settings()
    salaries = {k: v for k, v in settings.items() if str(k).startswith("Зарплата ")}
    if not salaries:
        return "Зарплата не указана в настройках."
    lines = ["Зарплата:"]
    lines.extend(f"{k}: {_money(float(v or 0))}" for k, v in salaries.items())
    return "\n".join(lines)


def _exec_get_mandatory_payments(args: dict, reader: "ExcelReader") -> str:
    payments = reader.get_mandatory_payments()
    half = str(args.get("half") or "all")
    halves = ["first", "second"] if half == "all" else [half]
    labels = {"first": "Первая зарплата", "second": "Вторая зарплата"}
    lines: list[str] = []
    for h in halves:
        items = payments.get(h, [])
        total = sum(float(i.get("amount") or 0) for i in items)
        lines.append(f"{labels.get(h, h)}: {_money(total)}")
        lines.extend(
            f"  {i.get('description')}: {_money(float(i.get('amount') or 0))}"
            for i in items
        )
    return "\n".join(lines) if lines else "Платежи не найдены."


def _exec_get_credits(reader: "ExcelReader") -> str:
    credits = reader.get_credits()
    if not credits:
        return "Кредиты не найдены."
    total_balance = sum(float(c.get("balance") or 0) for c in credits)
    total_payment = sum(float(c.get("monthly_payment") or 0) for c in credits)
    lines = [
        f"Кредиты — остаток долга: {_money(total_balance)}, "
        f"ежемесячные платежи: {_money(total_payment)}",
    ]
    lines.extend(
        f"  {c.get('name')}: долг {_money(float(c.get('balance') or 0))}, "
        f"платёж {_money(float(c.get('monthly_payment') or 0))}"
        for c in credits
    )
    return "\n".join(lines)


async def _exec_run_python_code(args: dict, reader: "ExcelReader") -> str:
    code = str(args.get("code") or "")
    if not code.strip():
        return "[error] Empty code."

    try:
        compile(code, "<agent>", "exec")
    except SyntaxError as exc:
        return f"[error] SyntaxError: {exc}"

    try:
        _guard(code)
    except ValueError as exc:
        return f"[error] Forbidden code: {exc}"

    safe_builtins = {
        name: getattr(_builtins_mod, name)
        for name in _SAFE_BUILTINS_NAMES
        if hasattr(_builtins_mod, name)
    }
    namespace: dict[str, Any] = {"reader": reader, "__builtins__": safe_builtins}

    def _run() -> Any:
        exec(code, namespace)  # noqa: S102
        return namespace.get("result")

    try:
        result = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, _run),
            timeout=5.0,
        )
    except asyncio.TimeoutError:
        return "[error] Execution timeout (5 s)."
    except Exception as exc:
        return f"[error] {type(exc).__name__}: {exc}"

    return repr(result)


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

async def execute_tool(name: str, raw_args: str | dict, reader: "ExcelReader", config: dict | None = None) -> str:
    """Harness entry point: validate args then run the appropriate executor."""
    try:
        args = _parse_args(raw_args)
    except Exception as exc:
        return f"[error] Could not parse tool arguments: {exc}"

    cfg = config or {}
    try:
        if name == "estimate_leave_impact":
            return await asyncio.to_thread(_exec_calculate_vacation_pay, args, reader, cfg)
        if name == "search_transactions":
            return await asyncio.to_thread(_exec_search_transactions, args, reader)
        if name == "aggregate_transactions":
            return await asyncio.to_thread(_exec_aggregate_transactions, args, reader)
        if name == "category_breakdown":
            return await asyncio.to_thread(_exec_category_breakdown, args, reader)
        if name == "analyze_cashflow":
            return await asyncio.to_thread(_exec_analyze_cashflow, args, reader)
        if name == "monthly_trend":
            return await asyncio.to_thread(_exec_monthly_trend, args, reader)
        if name == "get_capital":
            return await asyncio.to_thread(_exec_get_capital, reader)
        if name == "get_salary":
            return await asyncio.to_thread(_exec_get_salary, reader)
        if name == "get_mandatory_payments":
            return await asyncio.to_thread(_exec_get_mandatory_payments, args, reader)
        if name == "get_credits":
            return await asyncio.to_thread(_exec_get_credits, reader)
        if name == "run_python_code":
            return await _exec_run_python_code(args, reader)
        return f"[error] Unknown tool: {name}"
    except Exception as exc:
        logger.warning("Tool %s raised: %s", name, exc)
        return f"[error] {type(exc).__name__}: {exc}"
