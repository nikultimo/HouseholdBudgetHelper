import pytest
from unittest.mock import AsyncMock, MagicMock
from llm.router import (
    IntentClassification,
    _fast_classify,
    has_transaction_amount,
    is_leave_pay_query,
)


@pytest.mark.asyncio
async def test_classify_intent_calls_chat_structured():
    # Use a message that _fast_classify won't handle (no number → falls through to LLM)
    mock_result = IntentClassification(intent="question", extracted_value=None)
    mock_client = MagicMock()
    mock_client.chat_structured = AsyncMock(return_value=mock_result)

    from llm.router import classify_intent
    result = await classify_intent("сколько потратил в апреле?", mock_client, "test-router-model")

    assert result.intent == "question"
    mock_client.chat_structured.assert_called_once()
    kwargs = mock_client.chat_structured.call_args.kwargs
    assert kwargs["model_override"] == "test-router-model"
    assert kwargs["response_model"] == IntentClassification


@pytest.mark.asyncio
async def test_classify_intent_salary_update_has_extracted_value():
    mock_result = IntentClassification(intent="salary_update", extracted_value=80000.0)
    mock_client = MagicMock()
    mock_client.chat_structured = AsyncMock(return_value=mock_result)

    from llm.router import classify_intent
    result = await classify_intent("измени зарплату на 80000", mock_client, "test-model")

    assert result.intent == "salary_update"
    assert result.extracted_value == 80000.0


@pytest.mark.asyncio
async def test_classify_intent_unknown():
    mock_result = IntentClassification(intent="unknown", extracted_value=None)
    mock_client = MagicMock()
    mock_client.chat_structured = AsyncMock(return_value=mock_result)

    from llm.router import classify_intent
    result = await classify_intent("что-то непонятное", mock_client, "test-model")

    assert result.intent == "unknown"
    assert result.extracted_value is None


def test_fast_classify_detects_transaction():
    assert _fast_classify("кофе 200р") is not None
    assert _fast_classify("бензин 4500").intent == "transaction"
    assert _fast_classify("1000 предоплата аренда").intent == "transaction"


def test_fast_classify_ignores_questions():
    assert _fast_classify("сколько потратил 1000р?") is None
    assert _fast_classify("какой баланс?") is None


@pytest.mark.parametrize(
    "text",
    [
        "Траты с 14 по 19 июня какие были",
        "траты с 14 по 19 июня",
        "ТРАТЫ С 14 ПО 19 ИЮНЯ",
        "Расходы с 14 по 19 июня",
        "Сколько потратил денег с 10 по 18 июня",
    ],
)
def test_fast_classify_never_treats_expense_date_range_as_transaction(text):
    result = _fast_classify(text)
    assert result is not None
    assert result.intent == "question"


def test_fast_classify_ignores_salary_commands():
    assert _fast_classify("измени зарплату на 80000") is None
    assert _fast_classify("обнови капитал до 500000") is None


def test_fast_classify_ignores_no_description():
    assert _fast_classify("200") is None


def test_fast_classify_ignores_mandatory_payment_add_phrasing():
    # These must defer to the LLM router (mandatory_payment_add), not be
    # fast-classified as a one-time "transaction".
    assert _fast_classify("Добавь платёж в первую половину месяца - 5000 рублей на парковку") is None
    assert _fast_classify("добавь ежемесячный платёж 5000 на парковку") is None
    assert _fast_classify("новый регулярный платёж такси 3000 во вторую половину") is None


def test_transaction_amount_detection_requires_numeric_amount():
    assert has_transaction_amount("Билет на самолёт 5000") is True
    assert has_transaction_amount("Билет на самолёт, траты") is False


@pytest.mark.parametrize(
    "text",
    [
        "Какая выплата отпускных будет, отпуск с 22 по 27 июля",
        "Сколько получу 5 августа, если отпуск с 20 по 21 июля за свой счёт",
        "Отпуск с 10 по 19 июля, сколько денег получу",
    ],
)
def test_leave_pay_query_detection(text):
    assert is_leave_pay_query(text)
