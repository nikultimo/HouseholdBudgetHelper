from __future__ import annotations
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes
    from excel.reader import ExcelReader


def _parse_month_arg(args: list[str] | None, today: str) -> str:
    if args:
        candidate = args[0].strip()
        if len(candidate) == 7 and candidate[4] == "-" and candidate[:4].isdigit() and candidate[5:].isdigit():
            return candidate
    return today[:7]


async def cmd_stats(
    update: "Update",
    context: "ContextTypes.DEFAULT_TYPE",
    reader: "ExcelReader",
) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    month = _parse_month_arg(context.args if context.args else None, today)

    txns = reader.get_transactions(month)
    if not txns:
        await update.message.reply_text(f"Нет транзакций за {month}.")
        return

    by_cat: dict[str, float] = defaultdict(float)
    income = 0.0
    for t in txns:
        if t["type"] == "Расход":
            by_cat[t["category"]] += t["amount"]
        else:
            income += t["amount"]

    total = sum(by_cat.values())
    lines = [
        f"<b>📊 {month}</b>",
        f"Доходы: <b>+{income:,.0f} ₽</b>",
        f"Расходы: <b>-{total:,.0f} ₽</b>",
        f"Баланс: <b>{income - total:+,.0f} ₽</b>",
        "",
        "<b>По категориям:</b>",
    ]
    for cat, amt in sorted(by_cat.items(), key=lambda x: -x[1]):
        pct = amt / total * 100 if total else 0
        lines.append(f"  • <i>{cat}</i>: {amt:,.0f} ₽ ({pct:.0f}%)")

    await update.message.reply_html("\n".join(lines))
