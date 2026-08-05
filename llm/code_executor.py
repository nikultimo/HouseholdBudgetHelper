from __future__ import annotations
import asyncio
import builtins as _builtins_mod
from typing import TYPE_CHECKING, Any
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from llm.client import LLMClient
    from llm.tracing import TraceContext
    from excel.reader import ExcelReader


_READER_API_DOC = """
Available ExcelReader methods (use `reader` variable only):

reader.get_transactions(month: str | None = None) -> list[dict]
  # dict keys: date, month, description, category, type, whose, amount, account, mandatory
  # month format: "2026-04" — omit for all months

reader.get_last_transactions(n: int = 5) -> list[dict]

reader.get_categories() -> list[str]
reader.get_accounts() -> list[str]

reader.get_settings() -> dict[str, Any]
  # keys like: "Зарплата <Name>", "Капитал", "Доля <Name>"

reader.get_mandatory_payments() -> dict[str, list[dict]]
  # {"first": [...], "second": [...]}  each dict: {description, amount, due_day}

reader.get_credits() -> list[dict]
  # keys: name, type, balance, monthly_payment, rate

reader.get_summary_data(month: str) -> dict
  # keys: month, income, expenses_by_category, total_expenses, balance
"""

_SAFE_BUILTINS_NAMES = [
    "sum", "len", "sorted", "filter", "map", "min", "max", "round",
    "list", "dict", "str", "int", "float", "abs", "enumerate",
    "zip", "any", "all", "bool", "set", "tuple", "range",
]

_FORBIDDEN_PATTERNS = [
    "__", "import", "open(", "exec(", "eval(", "compile(", "globals(", "locals(",
]


class CodeSnippet(BaseModel):
    code: str = Field(description="Python snippet using `reader`. Store result in `result` variable.")
    explanation: str = Field(description="One sentence: what this code computes.")


def _guard(code: str) -> None:
    for pattern in _FORBIDDEN_PATTERNS:
        if pattern in code:
            raise ValueError(f"Forbidden pattern in generated code: '{pattern}'")


def _build_code_prompt(user_text: str) -> str:
    return f"""You are a Python code generator for a personal finance bot.
The user asked: "{user_text}"

You have access to `reader` (ExcelReader). Generate a Python snippet to answer the question.

{_READER_API_DOC}

Rules:
- Use ONLY `reader` methods and these builtins: {', '.join(_SAFE_BUILTINS_NAMES)}
- Do NOT use import, open, exec, eval, __dunder__, or any other names
- Store your final answer in a variable called `result`
- Prefer a single expression assigned to `result`
"""


async def handle_unknown_intent(
    text: str,
    reader: "ExcelReader",
    llm_client: "LLMClient",
    model: str,
    trace_ctx: "TraceContext | None" = None,
) -> str:
    snippet: CodeSnippet = await llm_client.chat_structured(
        messages=[
            {"role": "system", "content": _build_code_prompt(text)},
            {"role": "user", "content": text},
        ],
        response_model=CodeSnippet,
        model_override=model,
        trace_ctx=trace_ctx,
        span_name="code_gen",
    )

    try:
        _guard(snippet.code)
    except ValueError as e:
        return f"Не смог обработать запрос: {e}"

    safe_builtins = {
        name: getattr(_builtins_mod, name)
        for name in _SAFE_BUILTINS_NAMES
        if hasattr(_builtins_mod, name)
    }
    namespace: dict[str, Any] = {"reader": reader, "__builtins__": safe_builtins}

    def _run() -> Any:
        exec(snippet.code, namespace)  # noqa: S102
        return namespace.get("result")

    try:
        result = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, _run),
            timeout=5.0,
        )
    except asyncio.TimeoutError:
        return "Не смог обработать запрос: превышено время выполнения."
    except Exception as e:
        return f"Не смог обработать запрос: {e}"

    format_prompt = (
        f'The user asked (in Russian): "{text}"\n'
        f"A Python snippet computed: {result!r}\n"
        "Format as a clear, concise Russian answer. Use Telegram HTML (<b>bold</b> for key numbers). No markdown."
    )
    return await llm_client.chat(
        messages=[
            {"role": "system", "content": format_prompt},
            {"role": "user", "content": str(result)},
        ],
        model_override=model,
        trace_ctx=trace_ctx,
        span_name="code_format",
    )
