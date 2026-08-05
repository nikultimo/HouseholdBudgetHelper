from unittest.mock import MagicMock, patch
import os
import llm.tracing as tracing_mod


def _make_mock_lf():
    mock_lf = MagicMock()
    mock_span = MagicMock()
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_span)
    mock_cm.__exit__ = MagicMock(return_value=False)
    mock_lf.start_as_current_observation.return_value = mock_cm
    return mock_lf, mock_span, mock_cm


def test_start_trace_returns_noop_when_unavailable():
    with patch.object(tracing_mod, "_langfuse_client", None), \
         patch.object(tracing_mod, "_get_langfuse_client", return_value=None):
        ctx = tracing_mod.start_trace("user1", "hello")
    assert ctx._lf is None
    assert ctx._root_cm is None


def test_start_trace_creates_root_span():
    mock_lf, mock_span, mock_cm = _make_mock_lf()
    with patch.object(tracing_mod, "_langfuse_client", mock_lf):
        ctx = tracing_mod.start_trace("user1", "hello")
    mock_lf.start_as_current_observation.assert_called_once_with(
        name="request",
        as_type="span",
        input={"text": "hello"},
        metadata={"user_id": "user1"},
    )
    assert ctx._lf is mock_lf
    assert ctx._root_span is mock_span


def test_child_generation_records_span_with_usage_and_reasoning():
    mock_lf, mock_span, mock_cm = _make_mock_lf()
    ctx = tracing_mod.TraceContext(_lf=mock_lf, _root_cm=mock_cm, _root_span=mock_span)
    mock_lf.start_as_current_observation.reset_mock()

    ctx.child_generation(
        name="classify_intent",
        model="gpt-4",
        input={"text": "hello"},
        output={"intent": "question"},
        usage={"input": 10, "output": 5},
        reasoning="it's a question",
    )

    mock_lf.start_as_current_observation.assert_called_once_with(
        name="classify_intent", as_type="generation", model="gpt-4", input={"text": "hello"}
    )
    gen_span = mock_cm.__enter__.return_value
    gen_span.update.assert_called_once_with(
        output={"intent": "question"},
        usage_details={"input": 10, "output": 5},
        metadata={"reasoning": "it's a question"},
    )


def test_child_generation_omits_metadata_when_no_reasoning():
    mock_lf, mock_span, mock_cm = _make_mock_lf()
    ctx = tracing_mod.TraceContext(_lf=mock_lf, _root_cm=mock_cm, _root_span=mock_span)
    mock_lf.start_as_current_observation.reset_mock()

    ctx.child_generation(name="llm", model="m", input={}, output="ok", reasoning="")

    gen_span = mock_cm.__enter__.return_value
    gen_span.update.assert_called_once_with(
        output="ok",
        usage_details={},
        metadata=None,
    )


def test_child_generation_noop_when_no_lf():
    ctx = tracing_mod.TraceContext(_lf=None, _root_cm=None, _root_span=None)
    ctx.child_generation("foo", "m", {}, {})  # must not raise


def test_set_metadata_updates_root_span():
    mock_lf, mock_span, mock_cm = _make_mock_lf()
    ctx = tracing_mod.TraceContext(_lf=mock_lf, _root_cm=mock_cm, _root_span=mock_span)
    ctx.set_metadata("intent", "question")
    mock_span.update.assert_called_once_with(metadata={"intent": "question"})


def test_set_metadata_noop_when_no_lf():
    ctx = tracing_mod.TraceContext(_lf=None, _root_cm=None, _root_span=None)
    ctx.set_metadata("k", "v")  # must not raise


def test_finish_updates_output_closes_span_and_flushes():
    mock_lf, mock_span, mock_cm = _make_mock_lf()
    ctx = tracing_mod.TraceContext(_lf=mock_lf, _root_cm=mock_cm, _root_span=mock_span)
    ctx.finish(output={"reply": "done"})
    mock_span.update.assert_called_once_with(output={"reply": "done"})
    mock_cm.__exit__.assert_called_once_with(None, None, None)
    mock_lf.flush.assert_called_once()


def test_finish_noop_when_no_lf():
    ctx = tracing_mod.TraceContext(_lf=None, _root_cm=None, _root_span=None)
    ctx.finish()  # must not raise


def test_record_reply_persists_when_finish_has_no_output():
    mock_lf, mock_span, mock_cm = _make_mock_lf()
    ctx = tracing_mod.TraceContext(_lf=mock_lf, _root_cm=mock_cm, _root_span=mock_span)

    ctx.set_reply("<b>Точный ответ</b>")
    ctx.finish()

    mock_span.update.assert_called_once_with(output={"reply": "<b>Точный ответ</b>"})
    mock_cm.__exit__.assert_called_once_with(None, None, None)


def test_finish_does_not_replace_recorded_reply_with_legacy_output():
    mock_lf, mock_span, mock_cm = _make_mock_lf()
    ctx = tracing_mod.TraceContext(_lf=mock_lf, _root_cm=mock_cm, _root_span=mock_span)

    ctx.set_reply("Точный Telegram-ответ")
    ctx.finish(output="устаревший ответ")

    mock_span.update.assert_called_once_with(output={"reply": "Точный Telegram-ответ"})
    mock_cm.__exit__.assert_called_once_with(None, None, None)


def test_diagnostic_trace_read_retries_timeouts():
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"id": "trace-1", "output": {"reply": "ok"}}
    with patch.dict(os.environ, {
        "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
        "LANGFUSE_SECRET_KEY": "sk-lf-test",
        "LANGFUSE_HOST": "https://langfuse.example",
    }), patch("llm.tracing.httpx.get", side_effect=[tracing_mod.httpx.ReadTimeout("slow"), response]) as get:
        result = tracing_mod.read_trace_for_diagnostics(
            "trace-1", max_attempts=2, read_timeout_seconds=1.5, retry_delay_seconds=0,
        )

    assert result == {"id": "trace-1", "output": {"reply": "ok"}}
    assert get.call_count == 2
    assert get.call_args.kwargs["timeout"].read == 1.5


def test_langfuse_retries_after_cooldown_on_init_failure():
    tracing_mod.reset_langfuse_client()
    mock_lf, mock_span, mock_cm = _make_mock_lf()
    with patch.object(tracing_mod, "_langfuse_client", None), \
         patch.dict(os.environ, {
             "LANGFUSE_PUBLIC_KEY": "pk-lf-123456789012345678901",
             "LANGFUSE_SECRET_KEY": "sk-lf-123",
         }, clear=False), \
         patch("langfuse.get_client", side_effect=[Exception("boom"), mock_lf]) as get_client_mock, \
         patch.object(tracing_mod.time, "monotonic", side_effect=[0.0, 10.0, 61.0]):
        ctx1 = tracing_mod.start_trace("u1", "hello")
        ctx2 = tracing_mod.start_trace("u1", "hello")
        ctx3 = tracing_mod.start_trace("u1", "hello")

    assert ctx1._lf is None
    assert ctx2._lf is None
    assert ctx3._lf is mock_lf
    assert get_client_mock.call_count == 2
