from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from handlers.query import execute_budget_query, answer_schema_guided_query
from llm.query_planner import BudgetQueryPlan, TransactionFilters


def _reader_with_transactions():
    reader = MagicMock()
    reader.get_transactions.return_value = [
        {
            "date": date(2026, 4, 1),
            "month": "2026-04",
            "description": "coffee",
            "category": "Еда",
            "type": "Расход",
            "whose": "User1",
            "amount": 200.0,
            "account": "Main Card",
            "mandatory": "Нет",
            "row": 3,
        },
        {
            "date": date(2026, 4, 2),
            "month": "2026-04",
            "description": "groceries",
            "category": "Еда",
            "type": "Расход",
            "whose": "User1",
            "amount": 1800.0,
            "account": "Main Card",
            "mandatory": "Нет",
            "row": 4,
        },
        {
            "date": date(2026, 3, 20),
            "month": "2026-03",
            "description": "salary",
            "category": "Зарплата",
            "type": "Доход",
            "whose": "User1",
            "amount": 100000.0,
            "account": "Main Card",
            "mandatory": "Нет",
            "row": 5,
        },
    ]
    return reader


def test_execute_aggregate_transactions_sum():
    plan = BudgetQueryPlan(
        operation="aggregate_transactions",
        filters=TransactionFilters(month="2026-04", type="Расход", category="Еда"),
        aggregation="sum",
    )

    result = execute_budget_query(plan, _reader_with_transactions())

    assert result.supported is True
    assert "2,000 ₽" in result.answer


def test_execute_list_transactions_newest_first():
    plan = BudgetQueryPlan(
        operation="list_transactions",
        filters=TransactionFilters(month="2026-04"),
        limit=10,
    )

    result = execute_budget_query(plan, _reader_with_transactions())

    assert result.supported is True
    assert result.answer.index("groceries") < result.answer.index("coffee")


def test_execute_category_breakdown():
    plan = BudgetQueryPlan(
        operation="category_breakdown",
        filters=TransactionFilters(month="2026-04", type="Расход"),
    )

    result = execute_budget_query(plan, _reader_with_transactions())

    assert result.supported is True
    assert "Еда" in result.answer
    assert "2,000 ₽" in result.answer


def test_execute_compare_periods():
    plan = BudgetQueryPlan(
        operation="compare_periods",
        filters=TransactionFilters(month="2026-04", type="Расход"),
        compare_filters=TransactionFilters(month="2026-03", type="Расход"),
        aggregation="sum",
    )

    result = execute_budget_query(plan, _reader_with_transactions())

    assert result.supported is True
    assert "2026-04" in result.answer
    assert "Разница" in result.answer


def test_execute_settings_lookup_capital():
    reader = MagicMock()
    reader.get_capital_data.return_value = {
        "2026-03": {"rubles": 1000},
        "2026-04": {"rubles": 2500},
    }
    plan = BudgetQueryPlan(operation="settings_lookup", lookup="capital")

    result = execute_budget_query(plan, reader)

    assert result.supported is True
    assert "2026-04" in result.answer
    assert "2,500 ₽" in result.answer


def test_execute_unsupported_plan():
    plan = BudgetQueryPlan(operation="unsupported", reason="advice")

    result = execute_budget_query(plan, _reader_with_transactions())

    assert result.supported is False
    assert "advice" in result.answer


@pytest.mark.asyncio
async def test_answer_schema_guided_query_uses_router_model():
    plan = BudgetQueryPlan(
        operation="aggregate_transactions",
        filters=TransactionFilters(month="2026-04", type="Расход"),
        aggregation="sum",
    )
    llm_client = MagicMock()
    llm_client.chat_structured = AsyncMock(return_value=plan)

    result = await answer_schema_guided_query(
        "сколько потратил в апреле?",
        reader=_reader_with_transactions(),
        llm_client=llm_client,
        router_model="router-model",
        today="2026-04-30",
    )

    assert result.supported is True
    assert "2,000 ₽" in result.answer
    assert llm_client.chat_structured.call_args.kwargs["model_override"] == "router-model"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "operation", "date_from", "date_to", "month", "as_of"),
    [
        ("Сколько было денег 14 июня", "balance_at_date", None, None, None, "2026-06-14"),
        ("Траты с 14 по 19 июня какие были", "expense_period_summary", "2026-06-14", "2026-06-19", None, None),
        ("траты с 14 по 19 июня", "expense_period_summary", "2026-06-14", "2026-06-19", None, None),
        ("Сколько потратил денег с 10 по 18 июня", "expense_period_summary", "2026-06-10", "2026-06-18", None, None),
        ("Траты за июнь", "expense_period_summary", None, None, "2026-06", None),
        ("Баланс в июне", "balance_at_date", None, None, None, "2026-06-01"),
        ("Баланс в июле", "balance_at_date", None, None, None, "2026-07-01"),
        ("Баланс к декабрю", "projected_capital", None, None, None, "2026-12-01"),
        ("У меня 3 карты, какой был баланс в июне", "balance_at_date", None, None, None, "2026-06-01"),
    ],
)
async def test_common_russian_queries_use_deterministic_fast_plan(
    question, operation, date_from, date_to, month, as_of,
):
    from llm.query_planner import plan_budget_query

    llm_client = MagicMock()
    plan = await plan_budget_query(question, llm_client, "unused", "2026-07-13")

    assert plan.operation == operation
    assert plan.filters.type == "Расход" if operation == "expense_period_summary" else True
    assert plan.filters.date_from == date_from
    assert plan.filters.date_to == date_to
    assert plan.filters.month == month
    assert plan.as_of_date == as_of
    llm_client.chat_structured.assert_not_called()


def test_balance_at_date_is_start_of_day():
    reader = MagicMock()
    reader.get_balance_at_start.return_value = 1234.0
    plan = BudgetQueryPlan(operation="balance_at_date", as_of_date="2026-06-14")

    result = execute_budget_query(plan, reader)

    assert result.supported is True
    assert "начало" in result.answer
    assert "1,234" in result.answer
    reader.get_balance_at_start.assert_called_once_with(date(2026, 6, 14))


def test_expense_period_summary_excludes_income_and_lists_transactions():
    reader = _reader_with_transactions()
    plan = BudgetQueryPlan(
        operation="expense_period_summary",
        filters=TransactionFilters(month="2026-04", type="Расход"),
    )

    result = execute_budget_query(plan, reader)

    assert result.supported is True
    assert "2,000" in result.answer
    assert "зарплат" not in result.answer.casefold()
    assert "Операции" in result.answer


def test_expense_period_summary_uses_inclusive_date_range():
    reader = MagicMock()
    reader.get_transactions.return_value = [
        {"date": date(2026, 6, 13), "type": "Расход", "amount": 10, "description": "before", "category": "Еда", "row": 1},
        {"date": date(2026, 6, 14), "type": "Расход", "amount": 20, "description": "start", "category": "Еда", "row": 2},
        {"date": date(2026, 6, 19), "type": "Расход", "amount": 30, "description": "end", "category": "Транспорт", "row": 3},
        {"date": date(2026, 6, 19), "type": "Доход", "amount": 1000, "description": "income", "category": "Доход", "row": 4},
        {"date": date(2026, 6, 20), "type": "Расход", "amount": 40, "description": "after", "category": "Еда", "row": 5},
    ]
    plan = BudgetQueryPlan(
        operation="expense_period_summary",
        filters=TransactionFilters(date_from="2026-06-14", date_to="2026-06-19", type="Расход"),
    )

    result = execute_budget_query(plan, reader)

    assert "50 ₽" in result.answer
    assert "операций: <b>2</b>" in result.answer
    assert "start" in result.answer and "end" in result.answer
    assert "before" not in result.answer and "after" not in result.answer and "income" not in result.answer


def test_projected_capital_uses_separate_reader_operation():
    reader = MagicMock()
    reader.get_projected_capital_at_start.return_value = 4567.0
    plan = BudgetQueryPlan(operation="projected_capital", as_of_date="2026-12-01")

    result = execute_budget_query(plan, reader, today="2026-07-13")

    assert result.supported is True
    assert "Прогноз" in result.answer
    assert "4,567" in result.answer
    reader.get_projected_capital_at_start.assert_called_once_with(
        date(2026, 12, 1), reference_date=date(2026, 7, 13),
    )
    reader.get_balance_at_start.assert_not_called()


def test_historical_balance_operation_refuses_future_date():
    reader = MagicMock()
    plan = BudgetQueryPlan(operation="balance_at_date", as_of_date="2026-12-01")

    result = execute_budget_query(plan, reader, today="2026-07-13")

    assert result.supported is False
    reader.get_balance_at_start.assert_not_called()
