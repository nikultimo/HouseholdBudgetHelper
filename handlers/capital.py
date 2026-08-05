from __future__ import annotations
import asyncio
from datetime import date
from typing import TYPE_CHECKING

from salary.calculator import calculate_payment
from handlers.salary import get_salary_from_settings

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes
    from excel.reader import ExcelReader
    from excel.writer import ExcelWriter
    from yadisk.sync import YadiskSync


def _build_months(today: date, count: int = 7) -> list[str]:
    months = []
    y, m = today.year, today.month
    for _ in range(count):
        months.append(f"{y}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return months


async def cmd_update_capital(
    update: "Update",
    context: "ContextTypes.DEFAULT_TYPE",
    reader: "ExcelReader",
    writer: "ExcelWriter",
    yadisk: "YadiskSync",
    default_user: str | None = None,
    pay_days: tuple[int, int] = (5, 20),
    payment_model: str = "working_days",
    payment_percentages: tuple[float, float] = (0.5, 0.5),
) -> None:
    reader.invalidate_cache()
    settings = await asyncio.to_thread(reader.get_settings)
    salary = get_salary_from_settings(settings, default_user)

    if salary <= 0:
        await update.message.reply_text(
            "Зарплата пользователя не указана в листе ⚙️ Настройки."
        )
        return

    mandatory_payments = await asyncio.to_thread(reader.get_mandatory_payments)
    today = date.today()
    months = _build_months(today)
    await update.message.chat.send_action("typing")

    lo, hi = pay_days
    results: list[tuple[str, float, float, float, float]] = []
    for month_str in months:
        y, m = int(month_str[:4]), int(month_str[5:7])
        info_lo = calculate_payment(
            date(y, m, lo),
            salary,
            [],
            pay_days=pay_days,
            payment_model=payment_model,
            payment_percentages=payment_percentages,
        )
        info_hi = calculate_payment(
            date(y, m, hi),
            salary,
            [],
            pay_days=pay_days,
            payment_model=payment_model,
            payment_percentages=payment_percentages,
        )
        if payment_model == "fixed_percent":
            coeff_lo = round(payment_percentages[0], 4)
            coeff_hi = round(payment_percentages[1], 4)
        else:
            coeff_lo = round(info_lo.working_days_period / info_lo.working_days_month, 4)
            coeff_hi = round(info_hi.working_days_period / info_hi.working_days_month, 4)
        results.append((month_str, info_lo.net_amount, coeff_lo, info_hi.net_amount, coeff_hi))

    updated, projected = await asyncio.to_thread(
        writer.update_capital_salary,
        months,
        salary,
        0.13,
        mandatory_payments,
        pay_days,
        payment_model,
        payment_percentages,
    )
    not_found = [m for m in months if m not in updated]

    lines = [f"✅ Обновлено {len(updated)} мес. в 📈 Капитал:\n"]
    for month_str, sal_lo, c_lo, sal_hi, c_hi in results:
        if month_str in updated:
            proj = projected.get(month_str)
            proj_str = f", капитал ≈ <b>{proj:,.0f} ₽</b>" if proj is not None else ""
            lines.append(
                f"{month_str}: ЗП {lo}го — <b>{sal_lo:,.0f} ₽</b> (коэфф {c_lo:.4f}), "
                f"ЗП {hi}го — <b>{sal_hi:,.0f} ₽</b> (коэфф {c_hi:.4f}){proj_str}"
            )

    if not_found:
        lines.append(f"\n⚠️ Не найдены в таблице: {', '.join(not_found)}")

    try:
        await yadisk.upload()
    except Exception:
        lines.append(
            "\n⚠️ Файл сохранён локально, но не удалось загрузить на Яндекс Диск."
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
