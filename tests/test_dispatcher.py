from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dispatcher import _run_deterministic_or_agent, dispatch
from llm.router import IntentClassification


def _dependencies():
    cfg = MagicMock()
    cfg.router_model = "router"
    return {
        "cfg": cfg,
        "reader": MagicMock(),
        "writer": MagicMock(),
        "yadisk": MagicMock(),
        "llm_client": MagicMock(),
        "pending": {},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intent", "text"),
    [
        ("question", "Траты за июнь"),
        ("transaction_search", "Траты с 14 по 19 июня какие были"),
        ("capital_query", "Сколько было денег 14 июня"),
    ],
)
async def test_exact_reads_route_to_deterministic_service(intent, text):
    with patch("dispatcher._run_deterministic_or_agent", new_callable=AsyncMock) as deterministic:
        await dispatch(
            MagicMock(), MagicMock(), IntentClassification(intent=intent),
            text, "1", "2026-07-13", MagicMock(), **_dependencies(),
        )

    deterministic.assert_awaited_once()


@pytest.mark.asyncio
async def test_undated_capital_query_keeps_current_capital_handler():
    with (
        patch("dispatcher._run_deterministic_or_agent", new_callable=AsyncMock) as deterministic,
        patch("dispatcher.settings_cmd.cmd_capital_query", new_callable=AsyncMock) as current_capital,
    ):
        await dispatch(
            MagicMock(), MagicMock(), IntentClassification(intent="capital_query"),
            "Сколько у меня денег?", "1", "2026-07-13", MagicMock(), **_dependencies(),
        )

    deterministic.assert_not_awaited()
    current_capital.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_routes_to_deterministic_service_before_agent():
    with (
        patch("dispatcher._run_deterministic_or_agent", new_callable=AsyncMock) as deterministic,
        patch("dispatcher._run_agent", new_callable=AsyncMock) as agent,
    ):
        await dispatch(
            MagicMock(), MagicMock(), IntentClassification(intent="unknown"),
            "Сколько было денег 14 июня", "1", "2026-07-13", MagicMock(), **_dependencies(),
        )

    deterministic.assert_awaited_once()
    agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_dated_balance_overrides_incorrect_router_intent():
    with (
        patch("dispatcher._run_deterministic_or_agent", new_callable=AsyncMock) as deterministic,
        patch("dispatcher.handle_transaction_text", new_callable=AsyncMock) as transaction,
    ):
        await dispatch(
            MagicMock(), MagicMock(), IntentClassification(intent="transaction"),
            "Сколько было денег 14 июня", "1", "2026-07-13", MagicMock(), **_dependencies(),
        )

    deterministic.assert_awaited_once()
    transaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_expense_date_range_overrides_incorrect_transaction_intent():
    with (
        patch("dispatcher._run_deterministic_or_agent", new_callable=AsyncMock) as deterministic,
        patch("dispatcher.handle_transaction_text", new_callable=AsyncMock) as transaction,
    ):
        await dispatch(
            MagicMock(), MagicMock(), IntentClassification(intent="transaction"),
            "Траты с 14 по 19 июня", "1", "2026-07-13", MagicMock(), **_dependencies(),
        )

    deterministic.assert_awaited_once()
    transaction.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intent",
    ["transaction", "payments_checklist", "capital_query", "unknown"],
)
async def test_leave_pay_query_overrides_incorrect_router_intent(intent):
    with (
        patch("dispatcher._run_agent", new_callable=AsyncMock) as agent,
        patch("dispatcher._run_deterministic_or_agent", new_callable=AsyncMock) as deterministic,
        patch("dispatcher.handle_transaction_text", new_callable=AsyncMock) as transaction,
        patch("dispatcher.settings_cmd.cmd_payments_checklist", new_callable=AsyncMock) as payments,
    ):
        await dispatch(
            MagicMock(), MagicMock(), IntentClassification(intent=intent),
            "Сколько получу 5 августа, если отпуск с 20 по 24 июля",
            "1", "2026-07-13", MagicMock(), **_dependencies(),
        )

    agent.assert_awaited_once()
    deterministic.assert_not_awaited()
    transaction.assert_not_awaited()
    payments.assert_not_awaited()


@pytest.mark.asyncio
async def test_transaction_without_amount_requests_clarification_without_pending():
    deps = _dependencies()
    trace_ctx = MagicMock()
    with (
        patch("dispatcher.handle_transaction_text", new_callable=AsyncMock) as transaction,
        patch("dispatcher.reply_to_update", new_callable=AsyncMock) as reply,
    ):
        await dispatch(
            MagicMock(), MagicMock(), IntentClassification(intent="transaction"),
            "Билет на самолёт, траты", "1", "2026-07-13", trace_ctx, **deps,
        )

    transaction.assert_not_awaited()
    reply.assert_awaited_once()
    assert "сумму" in reply.call_args.args[2].casefold()
    assert deps["pending"] == {}
    trace_ctx.set_metadata.assert_any_call("route", "transaction_clarification")
    trace_ctx.set_metadata.assert_any_call("answer_source", "clarification")


@pytest.mark.asyncio
async def test_transaction_with_amount_records_parse_route():
    trace_ctx = MagicMock()
    with patch("dispatcher.handle_transaction_text", new_callable=AsyncMock) as transaction:
        await dispatch(
            MagicMock(), MagicMock(), IntentClassification(intent="transaction"),
            "Кофе 250", "1", "2026-07-13", trace_ctx, **_dependencies(),
        )

    transaction.assert_awaited_once()
    trace_ctx.set_metadata.assert_any_call("route", "transaction_parse")
    trace_ctx.set_metadata.assert_any_call("answer_source", "transaction_parser")


@pytest.mark.asyncio
async def test_unsupported_transaction_search_requests_single_message_clarification():
    unsupported = MagicMock(supported=False)
    trace_ctx = MagicMock()
    with (
        patch("dispatcher.answer_schema_guided_query", new_callable=AsyncMock, return_value=unsupported),
        patch("dispatcher._run_agent", new_callable=AsyncMock) as agent,
        patch("dispatcher.reply_to_update", new_callable=AsyncMock) as reply,
    ):
        await _run_deterministic_or_agent(
            MagicMock(), MagicMock(), "Найди транзакцию", "2026-07-13",
            cfg=MagicMock(router_model="router"), reader=MagicMock(), llm_client=MagicMock(),
            trace_ctx=trace_ctx,
            unsupported_reply="Укажи критерий поиска в одном сообщении.",
            unsupported_route="transaction_search_clarification",
        )

    agent.assert_not_awaited()
    reply.assert_awaited_once()
    assert "одном сообщении" in reply.call_args.args[2]
    trace_ctx.set_metadata.assert_any_call("route", "transaction_search_clarification")
    trace_ctx.set_metadata.assert_any_call("answer_source", "clarification")
