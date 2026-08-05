"""Generate a deterministic synthetic Excel workbook for agent evaluation.

All data is generic — no personal names, real transactions, or private values.
Known ground-truth totals are documented in eval_cases.py.

Usage:
    python tests/eval/generate_eval_workbook.py
"""
from __future__ import annotations

import pathlib
import sys
from datetime import date

import openpyxl
from openpyxl.styles import Font

# Make repo root importable when running as a script
_REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from excel.constants import (
    SHEET_CAPITAL,
    SHEET_CREDITS,
    SHEET_PAYMENTS,
    SHEET_SETTINGS,
    SHEET_TRANSACTIONS,
)

EVAL_WORKBOOK_PATH = pathlib.Path(__file__).parent / "eval_budget.xlsx"

# ---------------------------------------------------------------------------
# Seed transactions (col layout mirrors ExcelReader: A=date, C=desc, D=cat,
# E=type, F=whose, G=amount, H=account, I=mandatory)
# Col B is the "YYYY-MM" string kept for schema completeness.
# ---------------------------------------------------------------------------
_TRANSACTIONS: list[tuple] = [
    # date,       month,     description,      category,         type,     whose,  amount, account,    mandatory
    (date(2026, 5,  1), "2026-05", "Кофейня",   "Еда и продукты", "Расход", "User1", 350,  "Основной", "Нет"),
    (date(2026, 5,  5), "2026-05", "Кофейня",   "Еда и продукты", "Расход", "User1", 420,  "Основной", "Нет"),
    (date(2026, 5, 10), "2026-05", "Кофейня",   "Еда и продукты", "Расход", "User1", 390,  "Основной", "Нет"),
    (date(2026, 5, 15), "2026-05", "Кофейня",   "Еда и продукты", "Расход", "User1", 310,  "Основной", "Нет"),
    (date(2026, 5, 20), "2026-05", "Кофейня",   "Еда и продукты", "Расход", "User1", 530,  "Основной", "Нет"),
    (date(2026, 5,  3), "2026-05", "Суши Бар",  "Еда и продукты", "Расход", "User1", 2800, "Основной", "Нет"),
    (date(2026, 5, 12), "2026-05", "Бензин",    "Транспорт",      "Расход", "User1", 3500, "Основной", "Нет"),
    (date(2026, 5, 18), "2026-05", "Кино",      "Развлечения",    "Расход", "User1", 900,  "Основной", "Нет"),
    (date(2026, 5, 22), "2026-05", "Урок (Другу)", "Развлечения", "Расход", "User1", 2500, "Основной", "Нет"),
    (date(2026, 5, 27), "2026-05", "Аптека",    "Здоровье",       "Расход", "User1", 1200, "Основной", "Нет"),
    (date(2026, 4, 10), "2026-04", "Кофейня",   "Еда и продукты", "Расход", "User1", 400,  "Основной", "Нет"),
    (date(2026, 4, 20), "2026-04", "Бензин",    "Транспорт",      "Расход", "User1", 4000, "Основной", "Нет"),
    (date(2026, 4, 25), "2026-04", "Зарплата",  "Доход",          "Доход",  "User1", 80000, "Основной", "Нет"),
]

# Known totals for verification and eval cases
KNOWN_TOTALS = {
    # e015: 70000 + (32000+34000-1300)*2 = 70000 + 64700*2 = 199400
    "capital_aug_2026_baseline": 199400,
    "кофейня_may_2026": 2000,     # 350+420+390+310+530
    "кофейня_all_time": 2400,     # +400 april
    "суши_бар_may_2026": 2800,
    "развлечения_may_2026": 3400, # 900+2500
    "другу_may_2026": 2500,
    "транспорт_may_2026": 3500,
    "здоровье_may_2026": 1200,
    "еда_may_2026": 4800,         # 2000 (Кофейня) + 2800 (Суши Бар)
    "total_expense_may_2026": 12900,  # 2000+2800+3500+900+2500+1200
    "total_expense_apr_2026": 4400,   # 400+4000
}


def generate(path: pathlib.Path = EVAL_WORKBOOK_PATH) -> pathlib.Path:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # remove default sheet

    _write_transactions(wb)
    _write_settings(wb)
    _write_payments(wb)
    _write_credits(wb)
    _write_capital(wb)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    return path


def _write_transactions(wb: openpyxl.Workbook) -> None:
    ws = wb.create_sheet(SHEET_TRANSACTIONS)
    # Row 1: title row (ignored by reader)
    ws.cell(1, 1, "Транзакции").font = Font(bold=True)
    # Row 2: header (ignored by reader, min_row=3)
    headers = ["Дата", "Месяц", "Описание", "Категория", "Тип", "Чья", "Сумма", "Счёт", "Обязательный"]
    for col, h in enumerate(headers, 1):
        ws.cell(2, col, h).font = Font(bold=True)
    # Rows 3+: data
    for i, (txn_date, month, desc, cat, txn_type, whose, amount, account, mandatory) in enumerate(_TRANSACTIONS, start=3):
        ws.cell(i, 1, txn_date)
        ws.cell(i, 2, month)
        ws.cell(i, 3, desc)
        ws.cell(i, 4, cat)
        ws.cell(i, 5, txn_type)
        ws.cell(i, 6, whose)
        ws.cell(i, 7, amount)
        ws.cell(i, 8, account)
        ws.cell(i, 9, mandatory)


def _write_settings(wb: openpyxl.Workbook) -> None:
    ws = wb.create_sheet(SHEET_SETTINGS)
    settings = [
        ("Зарплата User1", 80000),
        ("Ставка НДФЛ", 0.13),
        ("Счёт по умолчанию", "Основной"),
        ("Категории", "Еда и продукты,Транспорт,Развлечения,Здоровье,Доход"),
    ]
    for row, (key, value) in enumerate(settings, start=1):
        ws.cell(row, 1, key)
        ws.cell(row, 2, value)


def _write_payments(wb: openpyxl.Workbook) -> None:
    ws = wb.create_sheet(SHEET_PAYMENTS)
    ws.cell(1, 1, "Платёж / назначение").font = Font(bold=True)
    ws.cell(1, 2, "День").font = Font(bold=True)
    ws.cell(1, 3, "Сумма").font = Font(bold=True)
    # First half (paid ~5th)
    ws.cell(2, 1, "Интернет")
    ws.cell(2, 2, 5)
    ws.cell(2, 3, 800)
    # Separator
    ws.cell(3, 1, "ВТОРАЯ ЗАРПЛАТА")
    # Second half (paid ~20th)
    ws.cell(4, 1, "Телефон")
    ws.cell(4, 2, 20)
    ws.cell(4, 3, 500)


def _write_credits(wb: openpyxl.Workbook) -> None:
    ws = wb.create_sheet(SHEET_CREDITS)
    ws.cell(1, 1, "Кредиты").font = Font(bold=True)
    headers = ["Название", "Тип", "Остаток", "Платёж/мес", "Ставка"]
    for col, h in enumerate(headers, 1):
        ws.cell(2, col, h).font = Font(bold=True)
    # One credit row starting at row 3
    ws.cell(3, 1, "Ипотека")
    ws.cell(3, 2, "Ипотека")
    ws.cell(3, 3, 2000000)
    ws.cell(3, 4, 20000)
    ws.cell(3, 5, 0.12)


def _write_capital(wb: openpyxl.Workbook) -> None:
    ws = wb.create_sheet(SHEET_CAPITAL)
    ws.cell(1, 1, "Капитал").font = Font(bold=True)
    ws.cell(2, 1, "Месяц").font = Font(bold=True)
    ws.cell(2, 2, "Баланс").font = Font(bold=True)
    # 3 month rows (reader reads from row 3, needs col A=month, col B=rubles, L=sal1, M=sal2)
    months = [
        ("2026-03", 50000, 32000, 34000),
        ("2026-04", 60000, 32000, 34000),
        ("2026-05", 70000, 32000, 34000),
        ("2026-06", 70000, 32000, 34000),  # current month for e015 (today=2026-06-11)
        ("2026-07",     0, 32000, 34000),  # future: salary cols filled, balance=0
        ("2026-08",     0, 32000, 34000),
    ]
    for row_idx, (month, balance, sal1, sal2) in enumerate(months, start=3):
        ws.cell(row_idx, 1, month)
        ws.cell(row_idx, 2, balance)
        ws.cell(row_idx, 12, sal1)  # col L
        ws.cell(row_idx, 13, sal2)  # col M


if __name__ == "__main__":
    path = generate()
    wb = openpyxl.load_workbook(str(path), data_only=True)
    print(f"Generated: {path}")
    for ws in wb.worksheets:
        print(f"  {ws.title}: {ws.max_row} rows")
    print("\nKnown totals:")
    for k, v in KNOWN_TOTALS.items():
        print(f"  {k}: {v:,} ₽")
