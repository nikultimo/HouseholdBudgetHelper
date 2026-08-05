from __future__ import annotations
from datetime import date
from typing import TYPE_CHECKING

from salary.calculator import calculate_payment, format_payment_info, next_payment_date

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes
    from excel.reader import ExcelReader


def get_salary_from_settings(settings: dict, default_user: str | None = None) -> float:
    if default_user:
        salary = settings.get(f"Зарплата {default_user}")
        if salary is not None:
            return float(salary)

    salary_values = [
        float(value or 0)
        for key, value in settings.items()
        if isinstance(key, str) and key.startswith("Зарплата ")
    ]
    positive_salary_values = [value for value in salary_values if value > 0]
    if len(positive_salary_values) == 1:
        return positive_salary_values[0]
    if len(salary_values) == 1:
        return salary_values[0]
    return 0.0


def _get_salary(reader: "ExcelReader", default_user: str | None = None) -> float:
    reader.invalidate_cache()
    settings = reader.get_settings()
    return get_salary_from_settings(settings, default_user)


def _get_payments_for_date(reader: "ExcelReader", payment_day: int, pay_days: tuple[int, int]) -> list[dict]:
    payments = reader.get_mandatory_payments()
    return payments["first"] if payment_day == pay_days[0] else payments["second"]


async def cmd_salary(
    update: "Update",
    context: "ContextTypes.DEFAULT_TYPE",
    reader: "ExcelReader",
    default_user: str | None = None,
    pay_days: tuple[int, int] = (5, 20),
    payment_model: str = "working_days",
    payment_percentages: tuple[float, float] = (0.5, 0.5),
) -> None:
    args = context.args or []
    today = date.today()

    if args and args[0].isdigit() and int(args[0]) in pay_days:
        day = int(args[0])
        m, y = today.month, today.year
        # if today is past that day, use next month
        if today.day > day:
            m += 1
            if m > 12:
                m, y = 1, y + 1
        payment_date = date(y, m, day)
    else:
        payment_date = next_payment_date(today, pay_days)

    try:
        salary = _get_salary(reader, default_user)
        if salary == 0:
            await update.message.reply_text(
                "Зарплата пользователя не указана в листе ⚙️ Настройки."
            )
            return

        mandatory = _get_payments_for_date(reader, payment_date.day, pay_days)
        info = calculate_payment(
            payment_date,
            salary,
            mandatory,
            pay_days=pay_days,
            payment_model=payment_model,
            payment_percentages=payment_percentages,
        )
        text = format_payment_info(info)
        await update.message.reply_text(text, parse_mode="HTML")

    except Exception as e:
        await update.message.reply_text(f"Ошибка расчёта: {e}")
