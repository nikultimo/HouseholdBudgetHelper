from __future__ import annotations
import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes
    from excel.reader import ExcelReader
    from excel.writer import ExcelWriter
    from yadisk.sync import YadiskSync
    from config import Config
    from llm.tracing import TraceContext

from telegram_helpers import reply_to_update


async def cmd_salary_query(
    update: "Update",
    context: "ContextTypes.DEFAULT_TYPE",
    reader: "ExcelReader",
    trace_ctx: "TraceContext | None" = None,
) -> None:
    settings = await asyncio.to_thread(reader.get_settings)
    salaries = {k: v for k, v in settings.items() if k.startswith("Зарплата ")}
    if not salaries:
        await reply_to_update(
            update, context, "Зарплаты не найдены в настройках.", trace_ctx=trace_ctx,
        )
        return
    lines = [f"<b>{k}</b>: {float(v):,.0f} ₽" for k, v in salaries.items()]
    await reply_to_update(
        update, context, "💰 " + "\n".join(lines),
        parse_mode="HTML", trace_ctx=trace_ctx,
    )


async def cmd_salary_update(
    update: "Update",
    context: "ContextTypes.DEFAULT_TYPE",
    extracted_value: float,
    text: str,
    reader: "ExcelReader",
    writer: "ExcelWriter",
    yadisk: "YadiskSync",
    cfg: "Config",
    trace_ctx: "TraceContext | None" = None,
) -> None:
    settings = await asyncio.to_thread(reader.get_settings)
    salary_keys = [k for k in settings if k.startswith("Зарплата ")]
    key = f"Зарплата {cfg.default_user}"
    if len(salary_keys) == 1:
        key = salary_keys[0]
    for k in salary_keys:
        name = k.replace("Зарплата ", "")
        if name.lower() in text.lower():
            key = k
            break
    try:
        await asyncio.to_thread(writer.update_setting, key, extracted_value)
    except KeyError:
        await reply_to_update(
            update, context, f"Настройка '{key}' не найдена в таблице.",
            trace_ctx=trace_ctx,
        )
        return
    reader.invalidate_cache()
    try:
        await yadisk.upload()
    except Exception:
        pass
    await reply_to_update(
        update,
        context,
        f"✅ <b>{key}</b> обновлена: <b>{extracted_value:,.0f} ₽</b>",
        parse_mode="HTML",
        trace_ctx=trace_ctx,
    )


async def cmd_capital_query(
    update: "Update",
    context: "ContextTypes.DEFAULT_TYPE",
    reader: "ExcelReader",
    trace_ctx: "TraceContext | None" = None,
) -> None:
    capital_data = await asyncio.to_thread(reader.get_capital_data)
    if not capital_data:
        await reply_to_update(
            update,
            context,
            "Лист 📈 Капитал не найден или пуст.",
            parse_mode="HTML",
            trace_ctx=trace_ctx,
        )
        return
    current_month = datetime.now().strftime("%Y-%m")
    # Show only current month + 12 months ahead
    now = datetime.now()
    display_months = []
    y, m = now.year, now.month
    for _ in range(13):
        display_months.append(f"{y}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1

    payments = await asyncio.to_thread(reader.get_mandatory_payments)
    mandatory_total = sum(float(p.get("amount") or 0) for p in payments.get("first", []))
    mandatory_total += sum(float(p.get("amount") or 0) for p in payments.get("second", []))

    lines = ["💼 <b>Капитал по месяцам:</b>\n"]
    projected_balance: float | None = None

    for month in display_months:
        if month not in capital_data:
            continue
        row = capital_data[month]
        rubles = row.get("rubles") or 0
        usd = row.get("usd") or 0
        investments = row.get("investments") or 0
        crypto = row.get("crypto") or 0
        debts = row.get("debts") or 0
        total_assets = rubles + usd + investments + crypto

        is_current = month == current_month

        if is_current:
            net = total_assets - debts
            projected_balance = net
            lines.append(
                f"▶️ <b>{month}</b>: активы <b>{total_assets:,.0f} ₽</b>"
                + (f", долги −{debts:,.0f} ₽" if debts else "")
                + f" → чистый <b>{net:,.0f} ₽</b>"
            )
        else:
            # Future rows may contain stale projected balances in col B; use salary cols instead.
            sal_first = row.get("salary_first") or 0
            sal_second = row.get("salary_second") or 0
            if projected_balance is None or (sal_first == 0 and sal_second == 0):
                continue
            projected_balance = projected_balance + sal_first + sal_second - mandatory_total
            lines.append(
                f"    <b>{month}</b>: ~<b>{projected_balance:,.0f} ₽</b>"
                f" <i>(+{sal_first + sal_second:,.0f} зп −{mandatory_total:,.0f} платежи)</i>"
            )

    if len(lines) == 1:
        await reply_to_update(
            update,
            context,
            "Данные по капиталу не найдены в листе 📈 Капитал.",
            trace_ctx=trace_ctx,
        )
        return
    await reply_to_update(
        update, context, "\n".join(lines), parse_mode="HTML", trace_ctx=trace_ctx,
    )


async def cmd_capital_update(
    update: "Update",
    context: "ContextTypes.DEFAULT_TYPE",
    extracted_value: float,
    reader: "ExcelReader",
    writer: "ExcelWriter",
    yadisk: "YadiskSync",
    trace_ctx: "TraceContext | None" = None,
) -> None:
    from datetime import datetime
    today = datetime.now().date()
    current_month = today.strftime("%Y-%m")
    try:
        await asyncio.to_thread(writer.reconcile_capital, today, extracted_value)
    except ValueError:
        await reply_to_update(
            update,
            context,
            f"⚠️ Месяц <b>{current_month}</b> не найден в листе 📈 Капитал.",
            parse_mode="HTML",
            trace_ctx=trace_ctx,
        )
        return
    reader.invalidate_cache()
    try:
        await yadisk.upload()
    except Exception:
        pass
    await reply_to_update(
        update,
        context,
        f"✅ Капитал за <b>{current_month}</b> обновлён: <b>{extracted_value:,.0f} ₽</b>",
        parse_mode="HTML",
        trace_ctx=trace_ctx,
    )


async def cmd_payments_checklist(
    update: "Update",
    context: "ContextTypes.DEFAULT_TYPE",
    reader: "ExcelReader",
    pay_days: tuple[int, int] = (5, 20),
    payment_model: str = "working_days",
    payment_percentages: tuple[float, float] = (0.5, 0.5),
    trace_ctx: "TraceContext | None" = None,
) -> None:
    payments = await asyncio.to_thread(reader.get_mandatory_payments)
    first = payments.get("first", [])
    second = payments.get("second", [])
    lines = ["📋 <b>Обязательные платежи</b>\n"]
    total = 0.0
    if payment_model == "fixed_percent":
        lines.append(
            f"Модель зарплаты: {payment_percentages[0] * 100:.0f}% / {payment_percentages[1] * 100:.0f}%\n"
        )
    if first:
        lines.append(f"1️⃣ <b>До {pay_days[0]}-го числа:</b>")
        for p in first:
            lines.append(f"  ☐ {p['description']} — <b>{float(p['amount']):,.0f} ₽</b>")
            total += float(p["amount"])
    if second:
        lines.append(f"\n2️⃣ <b>До {pay_days[1]}-го числа:</b>")
        for p in second:
            lines.append(f"  ☐ {p['description']} — <b>{float(p['amount']):,.0f} ₽</b>")
            total += float(p["amount"])
    lines.append(f"\n<b>Итого: {total:,.0f} ₽</b>")
    await reply_to_update(
        update, context, "\n".join(lines), parse_mode="HTML", trace_ctx=trace_ctx,
    )
