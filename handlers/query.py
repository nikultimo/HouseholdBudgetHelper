from __future__ import annotations
import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from llm.query_planner import BudgetQueryPlan, TransactionFilters, plan_budget_query

if TYPE_CHECKING:
    from excel.reader import ExcelReader
    from llm.client import LLMClient
    from llm.tracing import TraceContext

logger = logging.getLogger(__name__)

_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class QueryAnswer:
    supported: bool
    answer: str


def _money(value: float) -> str:
    return f"{value:,.0f} ₽"


def _valid_month(value: str | None) -> bool:
    return value is None or bool(_MONTH_RE.match(value))


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    if not _DATE_RE.match(value):
        raise ValueError(f"Некорректная дата: {value}")
    return date.fromisoformat(value)


def _matches_text(value: Any, needle: str) -> bool:
    return needle.lower() in str(value or "").lower()


def _filter_transactions(txns: list[dict[str, Any]], filters: TransactionFilters) -> list[dict[str, Any]]:
    if not _valid_month(filters.month):
        raise ValueError(f"Некорректный месяц: {filters.month}")
    date_from = _parse_date(filters.date_from)
    date_to = _parse_date(filters.date_to)

    results = txns
    if filters.month:
        results = [t for t in results if t.get("month") == filters.month]
    if date_from:
        results = [t for t in results if t.get("date") and t["date"] >= date_from]
    if date_to:
        results = [t for t in results if t.get("date") and t["date"] <= date_to]
    if filters.category:
        results = [t for t in results if _matches_text(t.get("category"), filters.category)]
    if filters.type:
        results = [t for t in results if t.get("type") == filters.type]
    if filters.whose:
        results = [t for t in results if _matches_text(t.get("whose"), filters.whose)]
    if filters.account:
        results = [t for t in results if _matches_text(t.get("account"), filters.account)]
    if filters.mandatory:
        results = [t for t in results if t.get("mandatory") == filters.mandatory]
    if filters.description_contains:
        results = [t for t in results if _matches_text(t.get("description"), filters.description_contains)]
    if filters.amount_min is not None:
        results = [t for t in results if float(t.get("amount") or 0) >= filters.amount_min]
    if filters.amount_max is not None:
        results = [t for t in results if float(t.get("amount") or 0) <= filters.amount_max]
    return list(results)


def _period_label(filters: TransactionFilters) -> str:
    if filters.month:
        return filters.month
    if filters.date_from or filters.date_to:
        return f"{filters.date_from or 'начало'} — {filters.date_to or 'сегодня'}"
    return "всё время"


def _aggregate(txns: list[dict[str, Any]], aggregation: str | None) -> float:
    values = [float(t.get("amount") or 0) for t in txns]
    op = aggregation or "sum"
    if op == "count":
        return float(len(txns))
    if not values:
        return 0.0
    if op == "sum":
        return sum(values)
    if op == "average":
        return sum(values) / len(values)
    if op == "min":
        return min(values)
    if op == "max":
        return max(values)
    raise ValueError(f"Неподдерживаемая агрегация: {op}")


def _format_txn(t: dict[str, Any]) -> str:
    txn_date = t.get("date")
    date_str = txn_date.strftime("%d.%m.%Y") if hasattr(txn_date, "strftime") else str(txn_date)
    sign = "💸" if t.get("type") == "Расход" else "💰"
    return (
        f"{sign} <b>{_money(float(t.get('amount') or 0))}</b> — "
        f"<i>{t.get('description', '')}</i> [{t.get('category', '')}] {date_str}"
    )


def _execute_transaction_query(plan: BudgetQueryPlan, reader: "ExcelReader") -> QueryAnswer:
    txns = _filter_transactions(reader.get_transactions(), plan.filters)
    label = _period_label(plan.filters)

    if plan.operation == "aggregate_transactions":
        value = _aggregate(txns, plan.aggregation)
        if (plan.aggregation or "sum") == "count":
            return QueryAnswer(True, f"Найдено транзакций за <b>{label}</b>: <b>{int(value)}</b>.")
        return QueryAnswer(True, f"Итого за <b>{label}</b>: <b>{_money(value)}</b>.")

    if plan.operation == "list_transactions":
        results = sorted(txns, key=lambda t: (t.get("date"), t.get("row", 0)), reverse=True)[:plan.limit]
        if not results:
            return QueryAnswer(True, "Ничего не найдено по запросу.")
        total = sum(float(t.get("amount") or 0) for t in results if t.get("type") == "Расход")
        lines = [f"<b>Найдено: {len(results)}</b>"]
        if total:
            lines.append(f"Итого расходов в списке: <b>{_money(total)}</b>")
        lines.append("")
        lines.extend(_format_txn(t) for t in results)
        return QueryAnswer(True, "\n".join(lines))

    if plan.operation in {"category_breakdown", "monthly_trend"}:
        group_by = "category" if plan.operation == "category_breakdown" else "month"
        if plan.operation == "category_breakdown" and plan.filters.type is None:
            txns = [t for t in txns if t.get("type") == "Расход"]
        totals: dict[str, float] = {}
        for txn in txns:
            key = str(txn.get(group_by) or "Без значения")
            totals[key] = totals.get(key, 0.0) + float(txn.get("amount") or 0)
        if not totals:
            return QueryAnswer(True, "Нет данных для такого разреза.")
        items = sorted(totals.items(), key=lambda item: item[1], reverse=True)
        title = "Расходы по категориям" if group_by == "category" else "Динамика по месяцам"
        lines = [f"<b>{title} ({label})</b>"]
        lines.extend(f"{name}: <b>{_money(total)}</b>" for name, total in items[:plan.limit])
        return QueryAnswer(True, "\n".join(lines))

    if plan.operation == "compare_periods":
        if plan.compare_filters is None:
            return QueryAnswer(False, "Не хватает второго периода для сравнения.")
        first = _aggregate(txns, plan.aggregation)
        second_txns = _filter_transactions(reader.get_transactions(), plan.compare_filters)
        second = _aggregate(second_txns, plan.aggregation)
        delta = first - second
        first_label = _period_label(plan.filters)
        second_label = _period_label(plan.compare_filters)
        return QueryAnswer(
            True,
            f"<b>{first_label}</b>: {_money(first)}\n"
            f"<b>{second_label}</b>: {_money(second)}\n"
            f"Разница: <b>{_money(delta)}</b>",
        )

    return QueryAnswer(False, "Неподдерживаемая операция с транзакциями.")


def _execute_lookup(plan: BudgetQueryPlan, reader: "ExcelReader") -> QueryAnswer:
    if plan.operation == "settings_lookup":
        lookup = plan.lookup or "settings"
        if lookup == "categories":
            values = reader.get_categories()
            return QueryAnswer(True, "Категории:\n" + "\n".join(f"• {v}" for v in values))
        if lookup == "accounts":
            values = reader.get_accounts()
            return QueryAnswer(True, "Счета:\n" + "\n".join(f"• {v}" for v in values))
        if lookup == "capital":
            capital = reader.get_capital_data()
            if not capital:
                return QueryAnswer(True, "Данные по капиталу не найдены.")
            month = sorted(capital)[-1]
            value = capital[month].get("rubles") or 0
            return QueryAnswer(True, f"Капитал за <b>{month}</b>: <b>{_money(float(value))}</b>.")
        settings = reader.get_settings()
        if lookup == "salary":
            salaries = {k: v for k, v in settings.items() if str(k).startswith("Зарплата ")}
            if not salaries:
                return QueryAnswer(True, "Зарплата не указана в настройках.")
            lines = ["<b>Зарплата</b>"]
            lines.extend(f"{key}: <b>{_money(float(value or 0))}</b>" for key, value in salaries.items())
            return QueryAnswer(True, "\n".join(lines))
        lines = ["<b>Настройки</b>"]
        lines.extend(f"{key}: {value}" for key, value in settings.items())
        return QueryAnswer(True, "\n".join(lines[: plan.limit + 1]))

    if plan.operation == "payments_lookup":
        payments = reader.get_mandatory_payments()
        halves = ["first", "second"] if (plan.payments_half or "all") == "all" else [plan.payments_half or "first"]
        labels = {"first": "Первая зарплата", "second": "Вторая зарплата"}
        lines: list[str] = []
        for half in halves:
            items = payments.get(half, [])
            total = sum(float(item.get("amount") or 0) for item in items)
            lines.append(f"<b>{labels.get(half, half)}: {_money(total)}</b>")
            lines.extend(
                f"• {item.get('description')}: {_money(float(item.get('amount') or 0))}"
                for item in items[: plan.limit]
            )
        return QueryAnswer(True, "\n".join(lines))

    if plan.operation == "credits_lookup":
        credits = reader.get_credits()
        if not credits:
            return QueryAnswer(True, "Кредиты не найдены.")
        total_balance = sum(float(c.get("balance") or 0) for c in credits)
        total_payment = sum(float(c.get("monthly_payment") or 0) for c in credits)
        lines = [
            "<b>Кредиты</b>",
            f"Остаток долга: <b>{_money(total_balance)}</b>",
            f"Ежемесячные платежи: <b>{_money(total_payment)}</b>",
            "",
        ]
        lines.extend(
            f"• {c.get('name')}: долг {_money(float(c.get('balance') or 0))}, "
            f"платёж {_money(float(c.get('monthly_payment') or 0))}"
            for c in credits[: plan.limit]
        )
        return QueryAnswer(True, "\n".join(lines))

    return QueryAnswer(False, "Неподдерживаемый справочный запрос.")


def execute_budget_query(
    plan: BudgetQueryPlan,
    reader: "ExcelReader",
    today: str | date | None = None,
) -> QueryAnswer:
    if plan.operation == "unsupported":
        return QueryAnswer(False, plan.reason or "Запрос не поддерживается схемой.")
    if plan.operation in {
        "aggregate_transactions",
        "list_transactions",
        "category_breakdown",
        "monthly_trend",
        "compare_periods",
    }:
        return _execute_transaction_query(plan, reader)
    if plan.operation == "balance_at_date":
        try:
            target = _parse_date(plan.as_of_date)
        except ValueError as exc:
            return QueryAnswer(True, str(exc))
        if target is None:
            return QueryAnswer(False, "Не указана дата для расчёта баланса.")
        reference_date = date.fromisoformat(today) if isinstance(today, str) else today
        if reference_date is not None and target > reference_date:
            return QueryAnswer(False, "Будущая дата требует операции прогноза капитала.")
        balance = reader.get_balance_at_start(target)
        if balance is None:
            return QueryAnswer(
                True,
                f"Достоверная история до <b>{target.strftime('%d.%m.%Y')}</b> недоступна: дата раньше ledger anchor.",
            )
        return QueryAnswer(
            True,
            f"Капитал на начало <b>{target.strftime('%d.%m.%Y')}</b>: <b>{_money(balance)}</b>.",
        )
    if plan.operation == "projected_capital":
        try:
            target = _parse_date(plan.as_of_date)
            reference_date = date.fromisoformat(today) if isinstance(today, str) else (today or date.today())
        except ValueError as exc:
            return QueryAnswer(True, str(exc))
        if target is None:
            return QueryAnswer(False, "Не указана дата для прогноза капитала.")
        if target <= reference_date:
            return QueryAnswer(False, "Прогноз капитала применяется только к будущим датам.")
        balance = reader.get_projected_capital_at_start(target, reference_date=reference_date)
        if balance is None:
            return QueryAnswer(
                True,
                f"Недостаточно плановых данных для прогноза на начало <b>{target.strftime('%d.%m.%Y')}</b>.",
            )
        return QueryAnswer(
            True,
            f"Прогноз капитала на начало <b>{target.strftime('%d.%m.%Y')}</b>: <b>{_money(balance)}</b>.",
        )
    if plan.operation == "expense_period_summary":
        txns = _filter_transactions(reader.get_transactions(), plan.filters)
        txns = [txn for txn in txns if txn.get("type") == "Расход"]
        label = _period_label(plan.filters)
        total = sum(float(txn.get("amount") or 0) for txn in txns)
        categories: dict[str, float] = {}
        for txn in txns:
            category = str(txn.get("category") or "Без категории")
            categories[category] = categories.get(category, 0.0) + float(txn.get("amount") or 0)
        lines = [
            f"<b>Траты за {label}</b>",
            f"Итого: <b>{_money(total)}</b> · операций: <b>{len(txns)}</b>",
        ]
        if categories:
            lines.append("")
            lines.append("<b>По категориям</b>")
            lines.extend(
                f"• {name}: <b>{_money(value)}</b>"
                for name, value in sorted(categories.items(), key=lambda item: item[1], reverse=True)
            )
        visible = sorted(txns, key=lambda txn: (txn.get("date"), txn.get("row", 0)), reverse=True)[:plan.limit]
        if visible:
            lines.extend(["", "<b>Операции</b>"])
            lines.extend(_format_txn(txn) for txn in visible)
        if len(txns) > len(visible):
            lines.append(f"<i>Показано {len(visible)} из {len(txns)} операций.</i>")
        return QueryAnswer(True, "\n".join(lines))
    if plan.operation in {"settings_lookup", "payments_lookup", "credits_lookup"}:
        return _execute_lookup(plan, reader)
    return QueryAnswer(False, "Запрос не поддерживается.")


async def answer_schema_guided_query(
    text: str,
    reader: "ExcelReader",
    llm_client: "LLMClient",
    router_model: str,
    today: str,
    trace_ctx: "TraceContext | None" = None,
) -> QueryAnswer:
    try:
        plan = await plan_budget_query(text, llm_client, router_model, today, trace_ctx=trace_ctx)
        if (
            plan.operation == "balance_at_date"
            and plan.as_of_date is not None
            and date.fromisoformat(plan.as_of_date) > date.fromisoformat(today)
        ):
            plan = plan.model_copy(update={"operation": "projected_capital"})
        if trace_ctx is not None:
            trace_ctx.set_metadata("query_plan", plan.model_dump())
        return await asyncio.to_thread(execute_budget_query, plan, reader, today)
    except Exception as exc:
        logger.warning("schema-guided query failed: %s", type(exc).__name__)
        return QueryAnswer(False, "Не удалось построить точный запрос по данным бюджета.")
