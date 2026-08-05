import pytest
from unittest.mock import AsyncMock, MagicMock
from llm.code_executor import _guard, CodeSnippet


def test_guard_blocks_dunder():
    with pytest.raises(ValueError, match="Forbidden"):
        _guard("reader.__class__")


def test_guard_blocks_import():
    with pytest.raises(ValueError, match="Forbidden"):
        _guard("import os")


def test_guard_blocks_open():
    with pytest.raises(ValueError, match="Forbidden"):
        _guard("open('/etc/passwd')")


def test_guard_allows_safe_code():
    _guard("result = sum(t['amount'] for t in reader.get_transactions())")


@pytest.mark.asyncio
async def test_handle_unknown_runs_code_and_formats():
    from llm.code_executor import handle_unknown_intent

    reader = MagicMock()
    reader.get_transactions.return_value = [
        {"amount": 200, "category": "Food", "type": "Расход", "date": "2026-04-01",
         "description": "coffee", "whose": "User1", "account": "Main Card", "mandatory": "Нет", "month": "2026-04"},
    ]

    snippet = CodeSnippet(
        code="result = sum(t['amount'] for t in reader.get_transactions())",
        explanation="total spending",
    )
    llm_client = MagicMock()
    llm_client.chat_structured = AsyncMock(return_value=snippet)
    llm_client.chat = AsyncMock(return_value="Итого расходов: 200 ₽")

    result = await handle_unknown_intent("сколько всего потрачено?", reader, llm_client, "test-model")

    assert "200" in result
    llm_client.chat_structured.assert_called_once()
    llm_client.chat.assert_called_once()
    assert llm_client.chat_structured.call_args.kwargs["model_override"] == "test-model"
    assert llm_client.chat.call_args.kwargs["model_override"] == "test-model"


@pytest.mark.asyncio
async def test_handle_unknown_returns_error_on_forbidden_code():
    from llm.code_executor import handle_unknown_intent

    reader = MagicMock()
    snippet = CodeSnippet(code="import os; result = os.listdir('/')", explanation="bad")
    llm_client = MagicMock()
    llm_client.chat_structured = AsyncMock(return_value=snippet)
    llm_client.chat = AsyncMock()

    result = await handle_unknown_intent("плохой запрос", reader, llm_client, "test-model")

    assert "Не смог обработать" in result
    llm_client.chat.assert_not_called()
