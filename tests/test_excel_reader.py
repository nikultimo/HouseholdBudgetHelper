# tests/test_excel_reader.py
import shutil
import pytest
from datetime import date
from pathlib import Path
import openpyxl

from excel.constants import SHEET_CREDITS, SHEET_TRANSACTIONS
from excel.reader import ExcelReader

SAMPLE_FILE = Path("example/budget_example.xlsx")


@pytest.fixture
def reader(tmp_path):
    dest = tmp_path / "budget.xlsx"
    shutil.copy(SAMPLE_FILE, dest)
    return ExcelReader(str(dest))


def test_get_transactions_returns_list(reader):
    txns = reader.get_transactions()
    assert isinstance(txns, list)
    assert len(txns) > 0


def test_transaction_has_required_fields(reader):
    txn = reader.get_transactions()[0]
    for field in ["date", "description", "category", "type", "whose", "amount", "account", "mandatory"]:
        assert field in txn, f"Missing field: {field}"


def test_get_transactions_filters_by_month(reader):
    txns = reader.get_transactions(month="2026-04")
    assert len(txns) > 0
    assert all(t["month"] == "2026-04" for t in txns)


def test_get_categories_returns_known_categories(reader):
    cats = reader.get_categories()
    assert "Еда и продукты" in cats
    assert "Транспорт" in cats
    assert "Доход" in cats


def test_get_accounts_returns_known_accounts(reader):
    accounts = reader.get_accounts()
    assert "Main Card" in accounts


def test_get_last_transactions(reader):
    last = reader.get_last_transactions(3)
    assert len(last) <= 3


def test_get_last_transactions_sorted_newest_first(reader):
    wb = openpyxl.load_workbook(reader.path)
    ws = wb[SHEET_TRANSACTIONS]
    ws.append([date(2027, 1, 10), None, "old appended", "Food", "Расход", "Test", 100, "Main Card", "Нет"])
    ws.append([date(2027, 4, 10), None, "newer same day first", "Food", "Расход", "Test", 200, "Main Card", "Нет"])
    ws.append([date(2027, 4, 10), None, "newer same day second", "Food", "Расход", "Test", 300, "Main Card", "Нет"])
    ws.append([date(2027, 3, 10), None, "middle appended", "Food", "Расход", "Test", 400, "Main Card", "Нет"])
    wb.save(reader.path)
    reader.invalidate_cache()

    last = reader.get_last_transactions(4)

    assert [txn["description"] for txn in last] == [
        "newer same day second",
        "newer same day first",
        "middle appended",
        "old appended",
    ]


def test_get_credits(reader):
    credits = reader.get_credits()
    assert isinstance(credits, list)
    assert len(credits) > 0
    assert "name" in credits[0]
    assert "balance" in credits[0]
    assert "monthly_payment" in credits[0]


def test_get_credits_accepts_percent_rate_strings(reader):
    wb = openpyxl.load_workbook(reader.path)
    ws = wb[SHEET_CREDITS]
    ws.append(["Sample loan", "loan", 100000, 5000, "29.9%"])
    wb.save(reader.path)
    reader.invalidate_cache()

    credits = reader.get_credits()

    assert credits[-1]["rate"] == pytest.approx(29.9)


def test_projected_capital_at_month_start_uses_only_completed_intervening_months():
    reader = ExcelReader("unused.xlsx")
    reader.get_capital_data = lambda: {
        "2026-07": {
            "rubles": 10_000, "usd": 0, "investments": 0, "crypto": 0, "debts": 0,
            "salary_first": 99_999, "salary_second": 99_999,
        },
        **{
            f"2026-{month:02d}": {
                "rubles": None, "usd": None, "investments": None, "crypto": None, "debts": None,
                "salary_first": 1_000, "salary_second": 500,
            }
            for month in range(8, 13)
        },
    }
    reader.get_mandatory_payments = lambda: {
        "first": [{"amount": 60}],
        "second": [{"amount": 40}],
    }

    projected = reader.get_projected_capital_at_start(
        date(2026, 12, 1), reference_date=date(2026, 7, 13),
    )

    # August through November are included; current July and target December are not.
    assert projected == 15_600
