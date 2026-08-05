"""Inline edit/delete flows for existing transactions."""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import TYPE_CHECKING, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram_helpers import reply_to_update

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes
    from excel.reader import ExcelReader
    from llm.tracing import TraceContext

logger = logging.getLogger(__name__)

_EDIT_TTL_SEC = 600.0

EDIT_FIELDS: tuple[tuple[str, str], ...] = (
    ("description", "Название"),
    ("amount", "Сумма"),
    ("date", "Дата"),
    ("category", "Категория"),
    ("type", "Тип"),
    ("whose", "Кто"),
    ("account", "Счёт"),
    ("mandatory", "Обязательный"),
)

_FIELD_LABELS = dict(EDIT_FIELDS)

# Keyed by Telegram user id.
_pending_edit: dict[str, dict[str, Any]] = {}


def _is_expired(entry: dict[str, Any]) -> bool:
    return time.monotonic() - entry["created_at"] > _EDIT_TTL_SEC


def get_pending_edit(user_id: str) -> dict[str, Any] | None:
    entry = _pending_edit.get(user_id)
    if entry and _is_expired(entry):
        _pending_edit.pop(user_id, None)
        return None
    return entry


def set_pending_edit(user_id: str, entry: dict[str, Any]) -> None:
    entry["created_at"] = time.monotonic()
    _pending_edit[user_id] = entry


def clear_pending_edit(user_id: str) -> None:
    _pending_edit.pop(user_id, None)


def find_transaction(reader: "ExcelReader", entry_id_or_row: str | int) -> dict[str, Any] | None:
    row_candidate = int(entry_id_or_row) if str(entry_id_or_row).isdigit() else None
    for txn in reader.get_transactions():
        if txn.get("entry_id") == str(entry_id_or_row) or txn.get("row") == row_candidate:
            return dict(txn)
    return None


def _format_amount(value: Any) -> str:
    try:
        return f"{float(value):,.0f} ₽"
    except (TypeError, ValueError):
        return str(value)


def format_field_value(field: str, value: Any) -> str:
    if field == "amount":
        return _format_amount(value)
    if field == "date" and hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value or "")


def get_field_label(field: str) -> str:
    return _FIELD_LABELS.get(field, field)


def format_transaction_state(txn: dict[str, Any]) -> str:
    txn_date = txn.get("date")
    if hasattr(txn_date, "strftime"):
        date_str = txn_date.strftime("%Y-%m-%d")
    else:
        date_str = str(txn_date or "")

    return (
        "<b>Текущая транзакция</b>\n"
        f"Дата: <b>{date_str}</b>\n"
        f"Название: <b>{txn.get('description', '')}</b>\n"
        f"Категория: <b>{txn.get('category', '')}</b>\n"
        f"Тип: <b>{txn.get('type', '')}</b>\n"
        f"Кто: <b>{txn.get('whose', '')}</b>\n"
        f"Сумма: <b>{_format_amount(txn.get('amount'))}</b>\n"
        f"Счёт: <b>{txn.get('account', '')}</b>\n"
        f"Обязательный: <b>{txn.get('mandatory', '')}</b>"
    )


def _transaction_label(txn: dict[str, Any]) -> str:
    txn_date = txn.get("date")
    date_str = txn_date.strftime("%d.%m") if hasattr(txn_date, "strftime") else str(txn_date)
    description = str(txn.get("description", ""))[:20]
    return f"{date_str} · {description} · {_format_amount(txn.get('amount'))}"


def _mode_from_text(text: str) -> str:
    low = text.lower()
    delete_words = ("удали", "удалить", "удаление", "delete", "remove")
    return "delete" if any(word in low for word in delete_words) else "edit"


def build_edit_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(label, callback_data=f"edit_field:{field}")
            for field, label in EDIT_FIELDS[i:i + 2]
        ]
        for i in range(0, len(EDIT_FIELDS), 2)
    ]
    rows.append([
        InlineKeyboardButton("✅ Сохранить", callback_data="edit_save:0"),
        InlineKeyboardButton("❌ Отмена", callback_data="edit_cancel:0"),
    ])
    return InlineKeyboardMarkup(rows)


def build_delete_keyboard(entry_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🗑 Удалить", callback_data=f"delete_confirm:{entry_id}"),
        InlineKeyboardButton("❌ Отмена", callback_data="delete_cancel:0"),
    ]])


async def handle_transaction_edit(
    update: "Update",
    context: "ContextTypes.DEFAULT_TYPE",
    reader: "ExcelReader",
    pending: dict,
    text: str = "",
    trace_ctx: "TraceContext | None" = None,
) -> None:
    del pending
    mode = _mode_from_text(text)
    txns = reader.get_last_transactions(5)
    if not txns:
        await reply_to_update(
            update, context, "Нет транзакций для изменения.", trace_ctx=trace_ctx,
        )
        return

    callback_prefix = "delete_select" if mode == "delete" else "edit_select"
    buttons = [
        [InlineKeyboardButton(
            _transaction_label(t),
            callback_data=f"{callback_prefix}:{t.get('entry_id') or t.get('row', 0)}",
        )]
        for t in txns
    ]
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data=f"{mode}_cancel:0")])

    action = "удаления" if mode == "delete" else "редактирования"
    await reply_to_update(
        update,
        context,
        f"Выбери транзакцию для {action}:",
        reply_markup=InlineKeyboardMarkup(buttons),
        trace_ctx=trace_ctx,
    )


def apply_field_value(entry: dict[str, Any], raw_value: str) -> tuple[bool, str]:
    field = entry.get("field")
    txn = entry.get("txn") or {}
    value = raw_value.strip()
    if not field:
        return False, "Сначала выбери поле для изменения."

    try:
        if field == "amount":
            normalized = value.replace(" ", "").replace(",", ".")
            amount = float(normalized)
            if amount <= 0:
                return False, "Сумма должна быть больше нуля."
            txn[field] = amount
        elif field == "date":
            txn[field] = date.fromisoformat(value)
        elif field == "type":
            lowered = value.lower()
            if lowered not in {"расход", "доход"}:
                return False, "Тип должен быть `Расход` или `Доход`."
            txn[field] = "Доход" if lowered == "доход" else "Расход"
        elif field == "mandatory":
            lowered = value.lower()
            if lowered not in {"да", "нет"}:
                return False, "Обязательный платёж: `Да` или `Нет`."
            txn[field] = "Да" if lowered == "да" else "Нет"
        else:
            if not value:
                return False, "Значение не должно быть пустым."
            txn[field] = value
    except ValueError:
        if field == "date":
            return False, "Дата должна быть в формате YYYY-MM-DD."
        return False, "Не удалось разобрать значение."

    entry["txn"] = txn
    entry["field"] = None
    entry["created_at"] = time.monotonic()
    label = _FIELD_LABELS.get(field, field)
    return True, f"Поле «{label}» обновлено."
