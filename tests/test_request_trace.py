from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dispatcher import dispatch
from llm.router import IntentClassification
import llm.tracing as tracing_mod


@pytest.mark.asyncio
async def test_deterministic_request_trace_has_question_intent_plan_route_and_exact_reply():
    langfuse = MagicMock()
    root_span = MagicMock()
    root_cm = MagicMock()
    root_cm.__enter__.return_value = root_span
    root_cm.__exit__.return_value = False
    langfuse.start_as_current_observation.return_value = root_cm

    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    reader = MagicMock()
    reader.get_transactions.return_value = [
        {
            "date": date(2026, 6, 14), "month": "2026-06", "description": "Кофейня",
            "category": "Еда", "type": "Расход", "amount": 250.0, "row": 3,
        },
    ]
    cfg = MagicMock(router_model="router-model")
    question = "Траты с 14 по 19 июня"

    with patch.object(tracing_mod, "_langfuse_client", langfuse):
        trace_ctx = tracing_mod.start_trace("user-1", question)
        trace_ctx.set_metadata("intent", "question")
        await dispatch(
            update,
            context,
            IntentClassification(intent="question"),
            question,
            "user-1",
            "2026-07-13",
            trace_ctx,
            cfg=cfg,
            reader=reader,
            writer=MagicMock(),
            yadisk=MagicMock(),
            llm_client=MagicMock(),
            pending={},
        )
        trace_ctx.finish()

    langfuse.start_as_current_observation.assert_called_once_with(
        name="request",
        as_type="span",
        input={"text": question},
        metadata={"user_id": "user-1"},
    )
    sent_reply = update.message.reply_text.call_args.args[0]
    updates = [call.kwargs for call in root_span.update.call_args_list]
    assert {"intent": "question"} in [item["metadata"] for item in updates if "metadata" in item]
    assert any(
        item.get("metadata", {}).get("query_plan", {}).get("operation") == "expense_period_summary"
        for item in updates
    )
    assert {"route": "deterministic_query"} in [item["metadata"] for item in updates if "metadata" in item]
    assert {"output": {"reply": sent_reply}} in updates
    root_cm.__exit__.assert_called_once_with(None, None, None)
