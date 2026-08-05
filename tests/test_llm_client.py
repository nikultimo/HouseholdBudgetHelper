import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pydantic import BaseModel


class SimpleOutput(BaseModel):
    value: str


@pytest.mark.asyncio
async def test_chat_structured_returns_pydantic_model():
    mock_instructor_client = MagicMock()
    mock_instructor_client.chat.completions.create = AsyncMock(
        return_value=SimpleOutput(value="hello")
    )

    with patch("llm.client.instructor") as mock_instructor_mod, \
         patch("llm.client.openai.AsyncOpenAI"):
        mock_instructor_mod.from_openai.return_value = mock_instructor_client
        mock_instructor_mod.Mode.JSON = "JSON"

        from llm.client import LLMClient
        client = LLMClient(api_key="test", model="test-model")
        result = await client.chat_structured(
            messages=[{"role": "user", "content": "test"}],
            response_model=SimpleOutput,
        )

    assert isinstance(result, SimpleOutput)
    assert result.value == "hello"


@pytest.mark.asyncio
async def test_chat_returns_string():
    mock_raw_client = MagicMock()
    mock_raw_client.chat.completions.create = AsyncMock(
        return_value=MagicMock(choices=[MagicMock(message=MagicMock(content="answer text"))])
    )

    with patch("llm.client.openai.AsyncOpenAI", return_value=mock_raw_client), \
         patch("llm.client.instructor"):
        from llm.client import LLMClient
        client = LLMClient(api_key="test", model="test-model")
        # Replace the raw client directly
        client._raw_client = mock_raw_client
        result = await client.chat(messages=[{"role": "user", "content": "q"}])

    assert result == "answer text"


@pytest.mark.asyncio
async def test_transcribe_calls_whisper():
    mock_whisper_client = MagicMock()
    mock_whisper_client.audio.transcriptions.create = AsyncMock(
        return_value=MagicMock(text="купил кофе на 200 рублей")
    )

    with patch("llm.client.openai.AsyncOpenAI", return_value=mock_whisper_client), \
         patch("llm.client.instructor"):
        from llm.client import LLMClient
        client = LLMClient(
            api_key="test",
            model="test-model",
            whisper_api_key="wkey",
            whisper_base_url="https://api.openai.com/v1",
        )
        client._whisper_client = mock_whisper_client

        # Create a fake audio file
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(b"fake audio")
            tmp = f.name
        try:
            text = await client.transcribe(tmp)
        finally:
            os.unlink(tmp)

    assert text == "купил кофе на 200 рублей"


def test_update_model_changes_model():
    with patch("llm.client.openai.AsyncOpenAI"), patch("llm.client.instructor"):
        from llm.client import LLMClient
        client = LLMClient(api_key="test", model="old-model")
        client.update_model("new-model")
        assert client.model == "new-model"


@pytest.mark.asyncio
async def test_chat_structured_uses_model_override():
    from pydantic import BaseModel
    class Out(BaseModel):
        value: str

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=Out(value="ok"))

    with patch("llm.client.instructor") as mock_instr, patch("llm.client.openai.AsyncOpenAI"):
        mock_instr.from_openai.return_value = mock_client
        mock_instr.Mode.JSON = "JSON"
        from llm.client import LLMClient
        client = LLMClient(api_key="test", model="default-model")
        client._client = mock_client
        await client.chat_structured(
            messages=[{"role": "user", "content": "hi"}],
            response_model=Out,
            model_override="override-model",
        )
    kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "override-model"


@pytest.mark.asyncio
async def test_chat_vision_passes_image_content_block():
    from pydantic import BaseModel
    class Receipt(BaseModel):
        amount: float

    mock_instructor_client = MagicMock()
    mock_instructor_client.chat.completions.create = AsyncMock(return_value=Receipt(amount=299.0))

    with patch("llm.client.instructor") as mock_instr, patch("llm.client.openai.AsyncOpenAI"):
        mock_instr.from_openai.return_value = mock_instructor_client
        mock_instr.Mode.JSON = "JSON"
        from llm.client import LLMClient
        client = LLMClient(api_key="test", model="default")
        client._client = mock_instructor_client

        result = await client.chat_vision(
            image_b64="abc123",
            text_prompt="parse this receipt",
            response_model=Receipt,
            vision_model="google/gemini-2.5-flash",
        )

    assert result.amount == 299.0
    kwargs = mock_instructor_client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "google/gemini-2.5-flash"
    messages = kwargs["messages"]
    assert len(messages) == 1
    content = messages[0]["content"]
    assert any(b["type"] == "image_url" for b in content)
    assert any(b["type"] == "text" for b in content)
    img_block = next(b for b in content if b["type"] == "image_url")
    assert "data:image/jpeg;base64,abc123" in img_block["image_url"]["url"]


@pytest.mark.asyncio
async def test_chat_calls_child_generation_when_trace_ctx_provided():
    from unittest.mock import MagicMock as MM
    mock_raw_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content="answer"))]
    mock_resp.usage = MagicMock(prompt_tokens=10, completion_tokens=3)
    mock_raw_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    mock_trace_ctx = MM()
    mock_trace_ctx.child_generation = MM()

    with patch("llm.client.openai.AsyncOpenAI", return_value=mock_raw_client), \
         patch("llm.client.instructor"):
        from llm.client import LLMClient
        client = LLMClient(api_key="test", model="test-model")
        client._raw_client = mock_raw_client
        result = await client.chat(
            messages=[{"role": "user", "content": "q"}],
            trace_ctx=mock_trace_ctx,
            span_name="analyze_budget",
        )

    assert result == "answer"
    mock_trace_ctx.child_generation.assert_called_once()
    call_kwargs = mock_trace_ctx.child_generation.call_args.kwargs
    assert call_kwargs["name"] == "analyze_budget"
    assert call_kwargs["usage"] == {"input": 10, "output": 3}
    assert call_kwargs["output"] == "answer"


@pytest.mark.asyncio
async def test_chat_structured_calls_child_generation_with_create_with_completion():
    from unittest.mock import MagicMock as MM
    from pydantic import BaseModel

    class Out(BaseModel):
        value: str
        reasoning: str = ""

    parsed = Out(value="ok", reasoning="because ok")
    raw = MagicMock()
    raw.usage = MagicMock(prompt_tokens=20, completion_tokens=8)
    raw.choices = [MagicMock(message=MagicMock(content="ok"))]

    mock_instructor_client = MagicMock()
    mock_instructor_client.chat.completions.create_with_completion = AsyncMock(
        return_value=(parsed, raw)
    )

    mock_trace_ctx = MM()
    mock_trace_ctx.child_generation = MM()

    with patch("llm.client.instructor") as mock_instr, \
         patch("llm.client.openai.AsyncOpenAI"):
        mock_instr.from_openai.return_value = mock_instructor_client
        mock_instr.Mode.JSON = "JSON"
        from llm.client import LLMClient
        client = LLMClient(api_key="test", model="test-model")
        client._client = mock_instructor_client

        result = await client.chat_structured(
            messages=[{"role": "user", "content": "hi"}],
            response_model=Out,
            trace_ctx=mock_trace_ctx,
            span_name="parse_transaction",
        )

    assert result.value == "ok"
    mock_trace_ctx.child_generation.assert_called_once()
    call_kwargs = mock_trace_ctx.child_generation.call_args.kwargs
    assert call_kwargs["name"] == "parse_transaction"
    assert call_kwargs["usage"] == {"input": 20, "output": 8}
    assert "because ok" in call_kwargs["reasoning"]
