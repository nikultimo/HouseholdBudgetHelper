from __future__ import annotations
import asyncio
import re
from datetime import date, datetime
from typing import TYPE_CHECKING

from llm.prompts import build_analysis_prompt
from llm.ru_months import find_month
from handlers.salary import get_salary_from_settings
from salary.calculator import calculate_payment

if TYPE_CHECKING:
    from llm.client import LLMClient
    from llm.tracing import TraceContext
    from excel.reader import ExcelReader


def _previous_month_key(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    if m == 1:
        return f"{y - 1}-12"
    return f"{y}-{m - 1:02d}"


def _extract_salary_balance_target(
    question: str,
    pay_days: tuple[int, int],
    today: date | None = None,
) -> tuple[str, list[int]] | None:
    low = question.lower()
    if not re.search(r"\b(зп|зарплат)", low):
        return None
    if not re.search(r"\b(остат|баланс|останет|будет)", low):
        return None

    days = [day for day in pay_days if re.search(rf"(?<!\d){day}(?!\d)", low)]
    if not days:
        return None

    today = today or date.today()
    year = today.year
    month = today.month
    year_match = re.search(r"\b(20\d{2})\b", low)
    if year_match:
        year = int(year_match.group(1))
    found = find_month(low)
    if found:
        month = found[0]
    month_key = f"{year}-{month:02d}"
    return month_key, sorted(set(days))


def _salary_balance_answer(
    question: str,
    reader: "ExcelReader",
    default_user: str | None = None,
    pay_days: tuple[int, int] = (5, 20),
    payment_model: str = "working_days",
    payment_percentages: tuple[float, float] = (0.5, 0.5),
) -> str | None:
    target = _extract_salary_balance_target(question, pay_days)
    if target is None:
        return None

    month_key, requested_days = target
    salary = get_salary_from_settings(reader.get_settings(), default_user)
    if salary <= 0:
        return "Зарплата пользователя не указана в листе ⚙️ Настройки."

    capital = reader.get_capital_data()
    base_month = _previous_month_key(month_key)
    if base_month in capital:
        running_balance = float(capital[base_month].get("rubles") or 0)
        base_label = base_month
    elif month_key in capital:
        running_balance = float(capital[month_key].get("rubles") or 0)
        base_label = month_key
    else:
        return f"Месяц <b>{month_key}</b> не найден в листе 📈 Капитал."

    payments = reader.get_mandatory_payments()
    lo, hi = pay_days
    y, m = int(month_key[:4]), int(month_key[5:7])
    lines = [
        f"Расчёт по данным бюджета для <b>{month_key}</b>:",
        f"Стартовый баланс ({base_label}): <b>{running_balance:,.2f} ₽</b>",
    ]

    for day in (lo, hi):
        half_key = "first" if day == lo else "second"
        info = calculate_payment(
            date(y, m, day),
            salary,
            payments.get(half_key, []),
            pay_days=pay_days,
            payment_model=payment_model,
            payment_percentages=payment_percentages,
        )
        running_balance = round(running_balance + info.net_amount - info.mandatory_total, 2)
        if day in requested_days:
            lines.append(
                f"\n<b>{day:02d}.{m:02d}</b>: "
                f"+{info.net_amount:,.2f} ₽ зп − {info.mandatory_total:,.2f} ₽ платежи "
                f"= остаток <b>{running_balance:,.2f} ₽</b>"
            )

    return "\n".join(lines)


def _salary_context(
    reader: "ExcelReader",
    default_user: str | None = None,
    pay_days: tuple[int, int] = (5, 20),
    payment_model: str = "working_days",
    payment_percentages: tuple[float, float] = (0.5, 0.5),
) -> dict:
    try:
        from datetime import date as _date
        from salary.calculator import calculate_payment, next_payment_date
        settings = reader.get_settings()
        salary = get_salary_from_settings(settings, default_user)
        payments = reader.get_mandatory_payments()
        today = _date.today()
        next_pay = next_payment_date(today, pay_days)
        mandatory = payments["first"] if next_pay.day == pay_days[0] else payments["second"]
        info = calculate_payment(
            next_pay,
            salary,
            mandatory,
            pay_days=pay_days,
            payment_model=payment_model,
            payment_percentages=payment_percentages,
        )
        return {
            "monthly_salary": salary,
            "payment_model": payment_model,
            "payment_percentages": payment_percentages,
            "next_payment_date": next_pay.isoformat(),
            "next_payment_day": next_pay.day,
            "next_payment_net": info.net_amount,
            "next_payment_gross": info.gross_amount,
            "working_days_period": info.working_days_period,
            "working_days_month": info.working_days_month,
            "mandatory_payments_first_half": payments["first"],
            "mandatory_payments_second_half": payments["second"],
            "mandatory_total_next": info.mandatory_total,
            "remainder_after_mandatory": info.remainder,
        }
    except Exception:
        return {}


def _build_context(
    reader: "ExcelReader",
    default_user: str | None = None,
    pay_days: tuple[int, int] = (5, 20),
    payment_model: str = "working_days",
    payment_percentages: tuple[float, float] = (0.5, 0.5),
) -> dict:
    now = datetime.now()
    current_month = f"{now.year}-{now.month:02d}"
    prev_month_dt = (
        datetime(now.year, now.month - 1, 1)
        if now.month > 1
        else datetime(now.year - 1, 12, 1)
    )
    prev_month = f"{prev_month_dt.year}-{prev_month_dt.month:02d}"
    if hasattr(reader, "get_transactions_derived"):
        derived = reader.get_transactions_derived(
            current_month=current_month,
            previous_month=prev_month,
            recent_n=20,
        )
        return {
            "current_month_summary": derived["current_month_summary"],
            "previous_month_summary": derived["previous_month_summary"],
            "credits": reader.get_credits(),
            "recent_transactions": derived["recent_transactions"],
            "salary": _salary_context(reader, default_user, pay_days, payment_model, payment_percentages),
            "capital": reader.get_capital_data(),
        }
    return {
        "current_month_summary": reader.get_summary_data(current_month),
        "previous_month_summary": reader.get_summary_data(prev_month),
        "credits": reader.get_credits(),
        "recent_transactions": reader.get_last_transactions(20),
        "salary": _salary_context(reader, default_user, pay_days, payment_model, payment_percentages),
        "capital": reader.get_capital_data(),
    }


async def answer_question(
    question: str,
    llm_client: "LLMClient",
    reader: "ExcelReader",
    trace_ctx: "TraceContext | None" = None,
    default_user: str | None = None,
    pay_days: tuple[int, int] = (5, 20),
    payment_model: str = "working_days",
    payment_percentages: tuple[float, float] = (0.5, 0.5),
) -> str:
    deterministic_answer = await asyncio.to_thread(
        _salary_balance_answer,
        question,
        reader,
        default_user,
        pay_days,
        payment_model,
        payment_percentages,
    )
    if deterministic_answer is not None:
        return deterministic_answer

    context = await asyncio.to_thread(
        _build_context,
        reader,
        default_user,
        pay_days,
        payment_model,
        payment_percentages,
    )
    prompt = build_analysis_prompt(question=question, context=context)
    messages = [
        {"role": "system", "content": "You are a helpful financial assistant. Answer in Russian."},
        {"role": "user", "content": prompt},
    ]
    return await llm_client.chat(
        messages=messages,
        trace_ctx=trace_ctx,
        span_name="analyze_budget",
    )


async def answer_general_financial_advice(
    question: str,
    llm_client: "LLMClient",
    trace_ctx: "TraceContext | None" = None,
) -> str:
    messages = [
        {
            "role": "system",
            "content": (
                "You are a helpful personal finance assistant. Answer in Russian. "
                "Give general educational guidance and do not imply that you inspected "
                "the user's spreadsheet data."
            ),
        },
        {"role": "user", "content": question},
    ]
    return await llm_client.chat(
        messages=messages,
        trace_ctx=trace_ctx,
        span_name="general_financial_advice",
    )
