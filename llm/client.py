from __future__ import annotations
import asyncio
import io
import logging
from typing import Any, TYPE_CHECKING, Type, TypeVar
import openai
import instructor
from pydantic import BaseModel

if TYPE_CHECKING:
    from llm.tracing import TraceContext

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


def _extract_usage(completion: Any) -> dict | None:
    try:
        u = completion.usage
        if u is None:
            return None
        return {"input": u.prompt_tokens, "output": u.completion_tokens}
    except Exception:
        return None


def _extract_thinking(completion: Any) -> str:
    try:
        content = completion.choices[0].message.content
        if not isinstance(content, list):
            return ""
        return "\n".join(
            b.get("thinking", "") for b in content
            if isinstance(b, dict) and b.get("type") == "thinking" and b.get("thinking")
        )
    except Exception:
        return ""


class LLMClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        whisper_api_key: str = "",
        whisper_base_url: str = "https://api.openai.com/v1",
    ) -> None:
        self.model = model
        self._raw_client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
        )
        self._client = instructor.from_openai(self._raw_client, mode=instructor.Mode.JSON)
        self._whisper_client = (
            openai.AsyncOpenAI(api_key=whisper_api_key, base_url=whisper_base_url)
            if whisper_api_key
            else None
        )

    def update_model(self, model: str) -> None:
        self.model = model

    async def chat_structured(
        self,
        messages: list[dict[str, Any]],
        response_model: Type[T],
        max_retries: int = 2,
        model_override: str | None = None,
        trace_ctx: "TraceContext | None" = None,
        span_name: str = "llm_call",
    ) -> T:
        model = model_override or self.model
        if trace_ctx is not None:
            result, completion = await self._client.chat.completions.create_with_completion(
                model=model,
                messages=messages,
                response_model=response_model,
                max_retries=max_retries,
            )
            thinking = _extract_thinking(completion)
            schema_reasoning = getattr(result, "reasoning", "")
            reasoning = "\n".join(filter(None, [thinking, schema_reasoning]))
            output = (
                result.model_dump(mode="json", exclude={"reasoning"})
                if hasattr(result, "model_dump")
                else str(result)
            )
            trace_ctx.child_generation(
                name=span_name,
                model=model,
                input={"messages": messages},
                output=output,
                usage=_extract_usage(completion),
                reasoning=reasoning,
            )
            return result
        return await self._client.chat.completions.create(
            model=model,
            messages=messages,
            response_model=response_model,
            max_retries=max_retries,
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        trace_ctx: "TraceContext | None" = None,
        span_name: str = "llm_call",
        model_override: str | None = None,
    ) -> str:
        model = model_override or self.model
        resp = await self._raw_client.chat.completions.create(
            model=model,
            messages=messages,
        )
        content = resp.choices[0].message.content or ""
        if trace_ctx is not None:
            trace_ctx.child_generation(
                name=span_name,
                model=model,
                input={"messages": messages},
                output=content,
                usage=_extract_usage(resp),
                reasoning=_extract_thinking(resp),
            )
        return content

    async def chat_vision(
        self,
        image_b64: str,
        text_prompt: str,
        response_model: Type[T],
        vision_model: str,
        trace_ctx: "TraceContext | None" = None,
        span_name: str = "llm_call",
    ) -> T:
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                    },
                    {"type": "text", "text": text_prompt},
                ],
            }
        ]
        if trace_ctx is not None:
            result, completion = await self._client.chat.completions.create_with_completion(
                model=vision_model,
                messages=messages,
                response_model=response_model,
                max_retries=2,
            )
            thinking = _extract_thinking(completion)
            schema_reasoning = getattr(result, "reasoning", "")
            reasoning = "\n".join(filter(None, [thinking, schema_reasoning]))
            output = (
                result.model_dump(mode="json", exclude={"reasoning"})
                if hasattr(result, "model_dump")
                else str(result)
            )
            trace_ctx.child_generation(
                name=span_name,
                model=vision_model,
                input={"text_prompt": text_prompt, "has_image": True},
                output=output,
                usage=_extract_usage(completion),
                reasoning=reasoning,
            )
            return result
        return await self._client.chat.completions.create(
            model=vision_model,
            messages=messages,
            response_model=response_model,
            max_retries=2,
        )

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model_override: str | None = None,
        trace_ctx: "TraceContext | None" = None,
        span_name: str = "agent_turn",
    ) -> Any:
        """Call the model with tool definitions. Returns the raw completion object."""
        model = model_override or self.model
        resp = await self._raw_client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        if trace_ctx is not None:
            msg = resp.choices[0].message
            output: Any = msg.content or ""
            if msg.tool_calls:
                output = [
                    {"tool": tc.function.name, "args": tc.function.arguments}
                    for tc in msg.tool_calls
                ]
            trace_ctx.child_generation(
                name=span_name,
                model=model,
                input={"messages": messages},
                output=output,
                usage=_extract_usage(resp),
            )
        return resp

    async def transcribe(self, audio_path: str, language: str = "ru") -> str:
        if self._whisper_client is None:
            raise RuntimeError("Voice transcription is disabled (WHISPER_API_KEY is not set).")
        audio_bytes = await asyncio.to_thread(
            lambda: open(audio_path, "rb").read()
        )
        resp = await self._whisper_client.audio.transcriptions.create(
            model="whisper-1",
            file=("audio.oga", io.BytesIO(audio_bytes)),
            language=language,
        )
        return resp.text
