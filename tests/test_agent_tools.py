from unittest.mock import MagicMock, patch

import pytest

from llm.agent_tools import (
    _exec_analyze_cashflow,
    _exec_calculate_vacation_pay,
    execute_tool,
)


def _reader(settings):
    reader = MagicMock()
    reader.get_settings.return_value = settings
    reader.get_mandatory_payments.return_value = {"first": [], "second": []}
    return reader


def test_vacation_estimate_uses_default_household_member_and_net_salary_once():
    reader = _reader({"Зарплата User1": 100_000, "Зарплата User2": 300_000})
    config = {
        "default_user": "User1",
        "salary_pay_days": [5, 20],
        "salary_payment_model": "working_days",
        "salary_payment_percentages": [0.5, 0.5],
        "vacation_average_month_days": 29.3,
        "vacation_ndfl_rate": 0.13,
    }

    with patch("llm.agent_tools.fetch_calendar", return_value={}):
        result = _exec_calculate_vacation_pay(
            {"vacation_start": "2026-07-10", "vacation_end": "2026-07-10"},
            reader,
            config,
        )

    assert "User1" in result
    assert "3,413" in result
    assert "прокси-оценка" in result.casefold()
    assert "300,000" not in result


def test_vacation_estimate_refuses_ambiguous_member():
    reader = _reader({"Зарплата User1": 100_000, "Зарплата User2": 120_000})

    with patch("llm.agent_tools.fetch_calendar", return_value={}):
        result = _exec_calculate_vacation_pay(
            {"vacation_start": "2026-07-10", "vacation_end": "2026-07-12"},
            reader,
            {},
        )

    assert result.startswith("[clarification]")
    assert "участника" in result.casefold()


@pytest.mark.asyncio
async def test_estimate_leave_impact_tool_alias_uses_same_calculator():
    reader = _reader({"Зарплата User1": 100_000})

    with patch("llm.agent_tools.fetch_calendar", return_value={}):
        result = await execute_tool(
            "estimate_leave_impact",
            {"vacation_start": "2026-07-10", "vacation_end": "2026-07-12"},
            reader,
            {"default_user": "User1"},
        )

    assert "прокси-оценка" in result.casefold()


def test_cashflow_analysis_separates_observed_surplus_from_savings_promise():
    reader = MagicMock()
    reader.get_transactions.return_value = [
        {"month": "2026-05", "type": "Доход", "amount": 100_000, "category": "Доход"},
        {"month": "2026-05", "type": "Расход", "amount": 30_000, "category": "Еда"},
        {"month": "2026-05", "type": "Расход", "amount": 10_000, "category": "Транспорт"},
    ]

    result = _exec_analyze_cashflow({"months": ["2026-05"]}, reader)

    assert "Наблюдаемый денежный поток" in result
    assert "60,000" in result
    assert "не гарантированная сумма накоплений" in result
    assert "Еда" in result
