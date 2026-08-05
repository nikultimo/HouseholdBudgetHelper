"""Salary payment calculator using Russian production calendar."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from salary.calendar_parser import fetch_calendar, working_days_in_period


@dataclass
class PaymentInfo:
    payment_date: date       # 5th or 20th
    period_start: date       # period start (worked days)
    period_end: date         # period end
    working_days_period: int # working days in the paid period
    working_days_month: int  # total working days in the base month
    monthly_salary: float
    gross_amount: float      # before tax
    net_amount: float        # after ~13% NDFL
    mandatory_payments: list[dict[str, Any]]
    mandatory_total: float
    remainder: float
    payment_model: str = "working_days"
    payment_percentage: float | None = None


def _last_day_of_month(y: int, m: int) -> date:
    if m == 12:
        return date(y + 1, 1, 1) - timedelta(days=1)
    return date(y, m + 1, 1) - timedelta(days=1)


def _prev_month(y: int, m: int) -> tuple[int, int]:
    return (y - 1, 12) if m == 1 else (y, m - 1)


def calculate_payment(
    payment_date: date,
    monthly_salary: float,
    mandatory_payments: list[dict[str, Any]],
    ndfl_rate: float = 0.13,
    pay_days: tuple[int, int] = (5, 20),
    payment_model: str = "working_days",
    payment_percentages: tuple[float, float] = (0.5, 0.5),
) -> PaymentInfo:
    """
    payment_date: the date of payment (one of the two configured pay days).
    mandatory_payments: list of dicts with keys: description, amount, due_day
    pay_days: (lower_day, higher_day) — the two monthly pay dates, sorted ascending.
              lower_day pays for the second half of previous month (16th–end).
              higher_day pays for the first half of current month (1st–15th).
    """
    lower_day, higher_day = pay_days
    day = payment_date.day
    m, y = payment_date.month, payment_date.year

    if day == lower_day:
        # Pays for 16th..end of PREVIOUS month
        py, pm = _prev_month(y, m)
        period_start = date(py, pm, 16)
        period_end = _last_day_of_month(py, pm)
        month_start = date(py, pm, 1)
        month_end = _last_day_of_month(py, pm)
    elif day == higher_day:
        # Pays for 1st..15th of CURRENT month
        period_start = date(y, m, 1)
        period_end = date(y, m, 15)
        month_start = date(y, m, 1)
        month_end = _last_day_of_month(y, m)
    else:
        raise ValueError(f"payment_date day must be one of {pay_days}, got {day}")
    if payment_model == "working_days":
        cal_year = payment_date.year
        calendar = fetch_calendar(cal_year)
        # also need previous year if period spans December
        if payment_date.month == 1 and payment_date.day == lower_day:
            prev_cal = fetch_calendar(cal_year - 1)
            calendar = {**prev_cal, **calendar}

        working_period = working_days_in_period(period_start, period_end, calendar)
        working_month = working_days_in_period(month_start, month_end, calendar)
        if not working_month:
            raise ValueError("No working days found in month")
        daily_rate = monthly_salary / len(working_month)
        net = round(daily_rate * len(working_period), 2)
        payment_percentage = None
    elif payment_model == "fixed_percent":
        if len(payment_percentages) != 2:
            raise ValueError("payment_percentages must have exactly 2 elements")
        if abs(sum(payment_percentages) - 1.0) > 0.0001:
            raise ValueError("payment_percentages must sum to 1.0")
        payment_percentage = payment_percentages[0] if day == lower_day else payment_percentages[1]
        net = round(monthly_salary * payment_percentage, 2)
        working_period = []
        working_month = []
    else:
        raise ValueError(f"Unsupported salary payment model: {payment_model}")

    gross = round(net / (1 - ndfl_rate), 2)

    mandatory_total = sum(float(p.get("amount", 0)) for p in mandatory_payments)
    remainder = round(net - mandatory_total, 2)

    return PaymentInfo(
        payment_date=payment_date,
        period_start=period_start,
        period_end=period_end,
        working_days_period=len(working_period),
        working_days_month=len(working_month),
        monthly_salary=monthly_salary,
        gross_amount=round(gross, 2),
        net_amount=net,
        mandatory_payments=mandatory_payments,
        mandatory_total=mandatory_total,
        remainder=remainder,
        payment_model=payment_model,
        payment_percentage=payment_percentage,
    )


def next_payment_date(today: date | None = None, pay_days: tuple[int, int] = (5, 20)) -> date:
    """Return the next salary payment date based on the two configured pay days."""
    if today is None:
        today = date.today()
    lower_day, higher_day = pay_days
    y, m, d = today.year, today.month, today.day
    if d < lower_day:
        return date(y, m, lower_day)
    if d < higher_day:
        return date(y, m, higher_day)
    # after higher_day → next month lower_day
    if m == 12:
        return date(y + 1, 1, lower_day)
    return date(y, m + 1, lower_day)


def format_payment_info(info: PaymentInfo) -> str:
    """Format PaymentInfo as Telegram HTML."""
    period = f"{info.period_start.strftime('%d.%m')}–{info.period_end.strftime('%d.%m.%Y')}"
    lines = [
        f"💰 <b>Выплата {info.payment_date.strftime('%d.%m.%Y')}</b>",
        f"Период: {period}",
    ]
    if info.payment_model == "fixed_percent":
        pct = (info.payment_percentage or 0) * 100
        lines.extend([
            f"Модель: фиксированный процент — <b>{pct:.0f}%</b> от месячной суммы",
            f"Зарплата (чистыми): {info.monthly_salary:,.0f} ₽",
            f"К получению: <b>{info.net_amount:,.0f} ₽</b>",
        ])
    else:
        lines.extend([
            f"Рабочих дней: <b>{info.working_days_period}</b> из {info.working_days_month}",
            f"Оклад (чистыми): {info.monthly_salary:,.0f} ₽ → дневная ставка: {info.monthly_salary/info.working_days_month:,.0f} ₽",
            f"К получению: <b>{info.net_amount:,.0f} ₽</b>",
        ])
    if info.mandatory_payments:
        lines.append("")
        lines.append("📋 <b>Обязательные платежи:</b>")
        for p in info.mandatory_payments:
            lines.append(f"  • {p['description']} — <b>{float(p['amount']):,.0f} ₽</b>")
        lines.append(f"  Итого обязательных: <b>{info.mandatory_total:,.0f} ₽</b>")
    else:
        lines.append("\n📋 Обязательных платежей на этот период нет.")

    sign = "✅" if info.remainder >= 0 else "⚠️"
    lines.append(f"\n{sign} <b>Остаток: {info.remainder:,.0f} ₽</b>")
    if info.remainder < 0:
        lines.append("⚠️ Не хватает на обязательные платежи!")
    return "\n".join(lines)
