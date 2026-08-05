import pytest
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace


@pytest.mark.asyncio
async def test_answer_question_returns_string():
    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(return_value="В апреле вы потратили 15 000 ₽ на еду.")

    mock_reader = MagicMock()
    mock_reader.get_summary_data.return_value = {
        "month": "2026-04",
        "income": 185000,
        "expenses_by_category": {"Еда и продукты": 15000},
        "total_expenses": 15000,
        "balance": 170000,
    }
    mock_reader.get_credits.return_value = []
    mock_reader.get_last_transactions.return_value = []

    from handlers.analysis import answer_question
    result = await answer_question(
        question="Сколько потратили на еду в апреле?",
        llm_client=mock_llm,
        reader=mock_reader,
    )

    assert "15" in result or "еду" in result.lower()
    mock_llm.chat.assert_called_once()
    call_kwargs = mock_llm.chat.call_args.kwargs
    assert call_kwargs["span_name"] == "analyze_budget"


@pytest.mark.asyncio
async def test_answer_question_handles_salary_balance_deterministically():
    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(return_value="wrong")

    mock_reader = MagicMock()
    mock_reader.get_settings.return_value = {"Зарплата User1": 388890}
    mock_reader.get_capital_data.return_value = {
        "2026-04": {"rubles": 665657.0},
        "2026-05": {"rubles": 805432.05},
    }
    mock_reader.get_mandatory_payments.return_value = {
        "first": [{"description": "First", "amount": 130798.0}],
        "second": [{"description": "Second", "amount": 108083.0}],
    }

    from handlers.analysis import answer_question
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "handlers.analysis.calculate_payment",
            MagicMock(side_effect=[
                SimpleNamespace(net_amount=194445.0, mandatory_total=130798.0),
                SimpleNamespace(net_amount=184211.05, mandatory_total=108083.0),
            ]),
        )
        result = await answer_question(
            question="Сколько остаток 5 мая и 20 мая будет после зп",
            llm_client=mock_llm,
            reader=mock_reader,
            default_user="User1",
            pay_days=(5, 20),
        )

    assert "729,304.00 ₽" in result
    assert "805,432.05 ₽" in result
    mock_llm.chat.assert_not_called()
