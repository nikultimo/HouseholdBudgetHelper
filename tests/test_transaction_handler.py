from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm.schemas import TransactionInput


@pytest.fixture
def parsed_txn():
    return TransactionInput(
        date=date(2026, 4, 16),
        description="кофе",
        category="Еда и продукты",
        type="Расход",
        whose="User1",
        amount=200.0,
        account="Main Card",
        mandatory="Нет",
        confidence=0.95,
        reasoning="упомянут кофе — еда и продукты",
    )


@pytest.fixture
def parsed_txn_usd():
    return TransactionInput(
        date=date(2026, 4, 16),
        description="кофе",
        category="Еда и продукты",
        type="Расход",
        whose="User1",
        amount=50.0,
        original_currency="USD",
        original_amount=50.0,
        account="Main Card",
        mandatory="Нет",
        confidence=0.95,
        reasoning="упомянут кофе 50 USD",
    )


@pytest.mark.asyncio
async def test_parse_transaction_returns_model(parsed_txn):
    mock_llm = MagicMock()
    mock_llm.chat_structured = AsyncMock(return_value=parsed_txn)

    mock_reader = MagicMock()
    mock_reader.get_categories.return_value = ["Еда и продукты", "Бензин"]
    mock_reader.get_accounts.return_value = ["Main Card"]
    mock_reader.get_transactions.return_value = []

    from handlers.transaction import parse_transaction
    result = await parse_transaction(
        text="купил кофе 200р",
        llm_client=mock_llm,
        reader=mock_reader,
        default_user="User1",
        today="2026-04-16",
    )

    assert result.amount == 200.0
    assert result.category == "Еда и продукты"


@pytest.mark.asyncio
async def test_parse_transaction_converts_usd(parsed_txn_usd):
    mock_llm = MagicMock()
    mock_llm.chat_structured = AsyncMock(return_value=parsed_txn_usd)

    mock_reader = MagicMock()
    mock_reader.get_categories.return_value = ["Еда и продукты"]
    mock_reader.get_accounts.return_value = ["Main Card"]
    mock_reader.get_transactions.return_value = []

    from handlers.transaction import parse_transaction
    with patch("handlers.transaction.convert_to_rub", AsyncMock(return_value=(4500.0, 90.0))):
        result = await parse_transaction(
            text="кофе $50",
            llm_client=mock_llm,
            reader=mock_reader,
            default_user="User1",
            today="2026-04-16",
        )

    assert result.amount == 4500.0
    assert result.original_currency == "USD"
    assert result.original_amount == 50.0


@pytest.mark.asyncio
async def test_transaction_handler_reports_workbook_read_error_instead_of_model_timeout():
    from dispatcher import handle_transaction_text

    update = MagicMock()
    update.message.chat.send_action = AsyncMock()
    context = MagicMock()
    reader = MagicMock()
    reader.get_categories.side_effect = ValueError("Unable to read workbook")
    llm_client = MagicMock()

    with patch("dispatcher.reply_to_update", new_callable=AsyncMock) as reply:
        await handle_transaction_text(
            update,
            context,
            "3000 сервис",
            "1",
            "2026-09-04",
            MagicMock(),
            cfg=MagicMock(default_user="User1"),
            reader=reader,
            llm_client=llm_client,
            pending={},
        )

    llm_client.chat_structured.assert_not_called()
    reply.assert_awaited_once()
    assert "книг" in reply.call_args.args[2].casefold()
    assert "таймаут" not in reply.call_args.args[2].casefold()


def test_confirmed_history_reuses_dominant_category_and_account(parsed_txn):
    from handlers.transaction import _apply_confirmed_history

    parsed_txn.description = "Кофейня"
    parsed_txn.category = "Всякое"
    parsed_txn.account = "Cash"
    history = [
        {"description": "Кофейня", "type": "Расход", "category": "Еда", "account": "Main"}
        for _ in range(4)
    ] + [
        {"description": "Кофейня", "type": "Расход", "category": "Всякое", "account": "Cash"}
    ]

    result = _apply_confirmed_history(parsed_txn, history)

    assert result.category == "Еда"
    assert result.account == "Main"


def test_confirmed_history_requires_three_matches_and_eighty_percent(parsed_txn):
    from handlers.transaction import _apply_confirmed_history

    parsed_txn.description = "Магазин"
    parsed_txn.category = "Всякое"
    history = [
        {"description": "Магазин", "type": "Расход", "category": "Еда", "account": "Main"},
        {"description": "Магазин", "type": "Расход", "category": "Дом", "account": "Main"},
    ]

    result = _apply_confirmed_history(parsed_txn, history)

    assert result.category == "Всякое"


def test_format_transaction_for_display_rub(parsed_txn):
    from handlers.transaction import format_transaction_for_display
    text = format_transaction_for_display(parsed_txn)
    assert "200" in text
    assert "Еда и продукты" in text
    assert "User1" in text
    assert "RUB" in text


def test_format_transaction_for_display_usd():
    from handlers.transaction import format_transaction_for_display
    txn = TransactionInput(
        date=date(2026, 4, 16),
        description="кофе",
        category="Еда и продукты",
        type="Расход",
        whose="User1",
        amount=4500.0,
        original_currency="USD",
        original_amount=50.0,
        account="Main Card",
        mandatory="Нет",
        confidence=0.95,
        reasoning="test",
    )
    text = format_transaction_for_display(txn)
    assert "4" in text and "500" in text
    assert "USD" in text
    assert "50" in text
    assert "90" in text
