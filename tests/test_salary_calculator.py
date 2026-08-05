from datetime import date
from unittest.mock import patch

from salary.calculator import calculate_payment


def test_calculate_payment_fixed_percent_model():
    with patch("salary.calculator.fetch_calendar", return_value={}), \
         patch("salary.calculator.working_days_in_period", side_effect=[list(range(10)), list(range(20))]):
        info = calculate_payment(
            date(2026, 5, 5),
            100000,
            [{"description": "Rent", "amount": 10000}],
            pay_days=(5, 20),
            payment_model="fixed_percent",
            payment_percentages=(0.4, 0.6),
        )

    assert info.net_amount == 40000
    assert info.payment_model == "fixed_percent"
    assert info.payment_percentage == 0.4
    assert info.remainder == 30000


def test_calculate_payment_working_days_remains_default():
    with patch("salary.calculator.fetch_calendar", return_value={}), \
         patch("salary.calculator.working_days_in_period", side_effect=[list(range(10)), list(range(20))]):
        info = calculate_payment(date(2026, 5, 5), 100000, [], pay_days=(5, 20))

    assert info.net_amount == 50000
    assert info.payment_model == "working_days"
