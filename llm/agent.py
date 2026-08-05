"""Budget agent: tool-calling harness loop.

The model proposes tool calls; this harness validates, executes, records, and
returns observations. The model never touches the reader directly.

Harness budgets (enforced per turn):
  max_turns          = 6
  max_tool_calls     = 15  (cumulative across turns)
  max_wall_time_sec  = 45
  MAX_RESULT_CHARS   = 3000 per tool result (truncated before appending)
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from html.parser import HTMLParser
from typing import TYPE_CHECKING

from llm.agent_tools import execute_tool, get_tool_schemas
from llm.ru_months import MONTH_ALTERNATION, MONTH_STEMS, find_month

if TYPE_CHECKING:
    from excel.reader import ExcelReader
    from llm.client import LLMClient
    from llm.tracing import TraceContext

logger = logging.getLogger(__name__)

_MAX_TURNS = 6
_MAX_TOOL_CALLS = 15
_MAX_WALL_TIME_SEC = 45.0
_MAX_RESULT_CHARS = 3000
# Retries for stochastic provider finish_reason=error (Gemini MALFORMED_FUNCTION_CALL).
_MAX_MALFORMED_RETRIES = 3
_ALLOWED_HTML_TAGS = {"b", "i", "code"}

_STOP_MESSAGES = {
    "time_limit_reached": (
        "⚠️ Запрос занял слишком много времени. Попробуй сформулировать его короче."
    ),
    "tool_call_limit_reached": (
        "⚠️ Достигнут лимит вызовов инструментов. Попробуй упростить запрос."
    ),
    "step_limit_reached": (
        "⚠️ Достигнут лимит шагов анализа. Попробуй уточнить запрос."
    ),
    "no_final_answer": (
        "⚠️ Модель не вернула ответ. Попробуй ещё раз."
    ),
    "no_final_answer_or_tool_call": (
        "⚠️ Модель не смогла обработать запрос. Попробуй переформулировать."
    ),
}

# System prompt: stable content first (tool defs inline via get_tool_schemas),
# then static harness rules and tips, and only the volatile date/user at the end.
_STATIC_INSTRUCTIONS = """\
Ты — финансовый помощник для учёта личного бюджета. Отвечай ТОЛЬКО на русском языке.
Используй Telegram HTML-форматирование: <b>жирный</b>, <i>курсив</i>, <code>код</code>.
Не придумывай данные — вызывай инструменты для получения информации из бюджета.
НИКОГДА не отвечай "данных нет" или "я не могу" без предварительного вызова инструментов.
Никогда не проси пользователя сообщить финансовые данные вручную (баланс, доходы, расходы, капитал) — всё это доступно через инструменты. Для текущего баланса/капитала — вызови get_capital().
Всегда сначала попробуй поиск, и только если инструмент вернул пустой результат — сообщай об отсутствии данных.
Отвечай кратко и по делу. Итоговые суммы выделяй жирным шрифтом.
Если данных нет — так и скажи, не выдумывай.

Правила интерпретации дат (важно):
- "за последний месяц" / "за прошедший месяц" = последние 30 дней → используй date_from/date_to, НЕ поле month.
  Пример: сегодня 2026-05-30 → date_from="2026-05-01", date_to="2026-05-30".
- "за прошлый месяц" / "в прошлом месяце" = предыдущий календарный месяц → month="YYYY-MM" прошлого месяца.
- "за этот месяц" / "в этом месяце" = текущий календарный месяц → month="YYYY-MM" текущего.
- Явно названный месяц ("в мае", "за июнь") = этот календарный месяц в указанном году;
  если год не назван, используй текущий год.
- "за последние N дней" → date_from = сегодня минус N дней, date_to = сегодня.

Для слов "траты", "расходы", "потратил" всегда устанавливай type="Расход".
Для слов "доходы", "получил", "заработал" всегда устанавливай type="Доход".
Для вопросов "куда ушли деньги", "где деньги", "сколько реально могу откладывать"
ВСЕГДА вызывай analyze_cashflow. Называй income−expense только наблюдаемым денежным
потоком, не гарантированной суммой накоплений. Плановые зарплата−обязательные платежи
также не являются реалистичной суммой накоплений без обычных расходов.

Правила выбора полей фильтра:
- description_contains — место/магазин/товар: "кофе", "буханка", "пятёрочка", "яндекс", "ветеринарка".
  Используй это поле, когда пользователь называет место или что купил.
- whose — ТОЛЬКО плательщик из домохозяйства (имя из настроек бюджета). Кто тратил деньги.
  НЕ получатель, НЕ кафе/магазин. "Отдал <имя>" → получатель → description_contains="<имя>".
- category — категория расхода: "Еда", "Транспорт", "Развлечения".

SGR-стратегия (Search → Get broader → Respond):
Если первый вызов инструмента вернул "Ничего не найдено" или 0 ₽ —
1. Проверь, правильное ли поле использовал (место → description_contains, человек → whose).
2. Повтори запрос без фильтра по месяцу/дате, чтобы проверить наличие данных вообще.
Только после обеих проверок сообщай пользователю об отсутствии данных.

Вопросы про отпуск и отпускные — ВСЕГДА вызывай estimate_leave_impact. Передавай отдельно
оплачиваемый отпуск и один период "за свой счёт" через unpaid_leave_start/unpaid_leave_end,
если он назван. Не называй результат точным:
сохраняй предупреждение инструмента о прокси-оценке. Если вопрос также о деньгах/капитале
к конкретному месяцу — обязательно вызови get_capital() и дай прогноз, не спрашивая пользователя.
Вопросы про зарплату или размер выплат — ВСЕГДА вызывай get_salary перед ответом."""


class _TelegramHTMLValidator(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.invalid = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _ALLOWED_HTML_TAGS or attrs:
            self.invalid = True
            return
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack.pop() != tag:
            self.invalid = True


def _numeric_claims(text: str) -> set[str]:
    normalized = text.replace("\u00a0", " ")
    tokens = re.findall(r"(?<!\w)\d[\d ,.]*\d|(?<!\w)\d", normalized)
    claims: set[str] = set()
    for token in tokens:
        token = token.strip()
        if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", token):
            compact = re.sub(r"[.,]", "", token)
        elif re.fullmatch(r"\d+,\d{1,2}", token):
            # Russian-style decimal comma (e.g. "29,9"), not a thousands grouping.
            compact = token.replace(",", ".").strip(".")
        else:
            compact = re.sub(r"[\s,]", "", token).strip(".")
        if compact:
            claims.add(compact)
    return claims


def _validate_agent_answer(
    answer: str,
    tool_results: list[str],
    question: str,
) -> list[str]:
    """Return quality flags for unsupported numbers or invalid Telegram HTML."""
    flags: list[str] = []
    parser = _TelegramHTMLValidator()
    try:
        parser.feed(answer)
        parser.close()
    except Exception:
        parser.invalid = True
    if parser.invalid or parser.stack or "*" in answer or "__" in answer:
        flags.append("invalid_telegram_html")

    if tool_results:
        evidence_numbers = _numeric_claims("\n".join([question, *tool_results]))
        if _numeric_claims(answer) - evidence_numbers:
            flags.append("unsupported_numeric_claim")
        if any("прокси-оценка" in result.casefold() for result in tool_results):
            low = answer.casefold()
            has_limit = any(word in low for word in ("огранич", "не учтен", "не расчёт работодателя"))
            if "оцен" not in low or not has_limit:
                flags.append("missing_estimate_caveat")
    return flags


def _canonical_tool_fallback(tool_results: list[str]) -> str:
    """Return a safe, grounded response when model synthesis fails validation."""
    joined = "\n\n".join(result.strip() for result in tool_results if result.strip())
    if not joined:
        return _STOP_MESSAGES["no_final_answer"]
    escaped = html.escape(joined[:3600])
    return "<b>Данные из бюджета:</b>\n<code>" + escaped + "</code>"


def _cashflow_fast_args(question: str, today: str) -> dict | None:
    low = question.casefold()
    if not re.search(r"(куда|где).{0,30}деньг|реальн\w*.{0,20}отклады", low):
        return None
    year = int(today[:4])
    months = []
    for pattern, number in MONTH_STEMS:
        if re.search(rf"\b{pattern}\b", low):
            months.append(f"{year}-{number:02d}")
    if "сейчас" in low or "текущ" in low:
        months.append(today[:7])
    return {"months": list(dict.fromkeys(months))} if months else {}


def _leave_fast_args(question: str, today: str) -> dict | None:
    low = question.casefold()
    if not re.search(r"\b(?:отпуск\w*|отпускн\w*)\b", low):
        return None
    range_re = re.compile(
        rf"\bс\s+([0-3]?\d)\s+по\s+([0-3]?\d)\s+({MONTH_ALTERNATION})"
    )
    year_match = re.search(r"\b(20\d{2})\b", low)
    year = int(year_match.group(1)) if year_match else int(today[:4])
    result: dict[str, str] = {}
    for match in range_re.finditer(low):
        month_word = match.group(3)
        found = find_month(month_word)
        month = found[0]
        try:
            start = f"{year}-{month:02d}-{int(match.group(1)):02d}"
            end = f"{year}-{month:02d}-{int(match.group(2)):02d}"
        except ValueError:
            return None
        prefix = low[max(0, match.start() - 35):match.start()]
        explicitly_paid = bool(re.search(r"оплачиваем\w*\s+отпуск\w*\s*$", prefix))
        is_unpaid = not explicitly_paid and ("за свой сч" in prefix or "неоплач" in prefix)
        if is_unpaid:
            result["unpaid_leave_start"] = start
            result["unpaid_leave_end"] = end
        elif "vacation_start" not in result:
            result["vacation_start"] = start
            result["vacation_end"] = end
    return result if "vacation_start" in result else None


def _captured_fast_tool_messages(tool_results: list[tuple[str, dict, str]], answer: str) -> list[dict]:
    messages: list[dict] = []
    for index, (name, args, result) in enumerate(tool_results):
        call_id = f"fast-{name}-{index}"
        messages.extend([
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": str(args)},
                }],
            },
            {"role": "tool", "tool_call_id": call_id, "content": result},
        ])
    messages.append({"role": "assistant", "content": answer})
    return messages


def _build_system_prompt(tips: list[str], today: str, default_user: str) -> str:
    parts = [_STATIC_INSTRUCTIONS]
    if tips:
        parts.append("\nДополнительные инструкции пользователя:\n" + "\n".join(f"• {t}" for t in tips))
    parts.append(f"\nСегодня: {today}. Пользователь по умолчанию: {default_user}.")
    return "\n".join(parts)


async def _handle_tool_call(
    call: object,
    reader: "ExcelReader",
    trace_ctx: "TraceContext | None" = None,
    config: dict | None = None,
) -> str:
    """Harness executor: validate call, run tool, return observation string."""
    name = getattr(call, "function", None)
    if name is None:
        return "[error] Malformed tool call."
    tool_name = name.name
    raw_args = name.arguments
    result = await execute_tool(tool_name, raw_args, reader, config)
    logger.debug("Tool %s → %d chars", tool_name, len(result))
    if trace_ctx is not None:
        import json as _json
        try:
            args_display = _json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except Exception:
            args_display = raw_args
        trace_ctx.child_generation(
            name=f"tool:{tool_name}",
            model="(tool)",
            input={"tool": tool_name, "args": args_display},
            output=result[:_MAX_RESULT_CHARS],
        )
    return result


async def run_budget_agent(
    question: str,
    reader: "ExcelReader",
    llm_client: "LLMClient",
    tips: list[str],
    today: str,
    default_user: str,
    model: str,
    enable_code_tool: bool = False,
    trace_ctx: "TraceContext | None" = None,
    _capture_messages: list | None = None,
    agent_config: dict | None = None,
) -> str:
    """Run the tool-calling budget agent and return a final answer string.

    Pass a mutable list as _capture_messages to receive the full conversation
    history after the run (used by the eval harness to extract tool results).
    """
    fast_leave_args = _leave_fast_args(question, today)
    if fast_leave_args is not None:
        tool_results = [(
            "estimate_leave_impact",
            fast_leave_args,
            await execute_tool("estimate_leave_impact", fast_leave_args, reader, agent_config),
        )]
        if re.search(r"\b(?:капитал|сколько\s+денег\s+будет|к\s+\w+)", question.casefold()):
            capital_result = await execute_tool("get_capital", {}, reader, agent_config)
            tool_results.append(("get_capital", {}, capital_result))
        answer = _canonical_tool_fallback([result for _, _, result in tool_results])
        target_match = re.search(
            r"\bк\s+(январ\w*|феврал\w*|март\w*|апрел\w*|ма[йяе]|июн\w*|июл\w*|"
            r"август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)",
            question.casefold(),
        )
        if target_match:
            answer = f"<b>Оценка к {html.escape(target_match.group(1))}:</b>\n" + answer
        if trace_ctx is not None:
            for name, args, result in tool_results:
                trace_ctx.child_generation(
                    name=f"tool:{name}",
                    model="(tool)",
                    input={"tool": name, "args": args},
                    output=result[:_MAX_RESULT_CHARS],
                )
            trace_ctx.set_metadata("answer_source", "deterministic_agent_tool")
        if _capture_messages is not None:
            _capture_messages.extend(_captured_fast_tool_messages(tool_results, answer))
        return answer

    fast_cashflow_args = _cashflow_fast_args(question, today)
    if fast_cashflow_args is not None:
        result = await execute_tool(
            "analyze_cashflow", fast_cashflow_args, reader, agent_config,
        )
        answer = _canonical_tool_fallback([result])
        if trace_ctx is not None:
            trace_ctx.child_generation(
                name="tool:analyze_cashflow",
                model="(tool)",
                input={"tool": "analyze_cashflow", "args": fast_cashflow_args},
                output=result[:_MAX_RESULT_CHARS],
            )
            trace_ctx.set_metadata("answer_source", "deterministic_agent_tool")
        if _capture_messages is not None:
            _capture_messages.extend(_captured_fast_tool_messages(
                [("analyze_cashflow", fast_cashflow_args, result)], answer,
            ))
        return answer

    tools = get_tool_schemas(enable_code_tool=enable_code_tool)
    system_prompt = _build_system_prompt(tips, today, default_user)

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]

    tool_calls_used = 0
    deadline = time.monotonic() + _MAX_WALL_TIME_SEC
    _empty_answer_nudged = False
    _grounding_retry_used = False
    tool_evidence: list[str] = []

    for turn in range(_MAX_TURNS):
        if time.monotonic() > deadline:
            logger.warning("Agent budget: time limit reached on turn %d", turn)
            return _STOP_MESSAGES["time_limit_reached"]
        if tool_calls_used >= _MAX_TOOL_CALLS:
            logger.warning("Agent budget: tool call limit reached on turn %d", turn)
            return _STOP_MESSAGES["tool_call_limit_reached"]

        # Some providers (e.g. Gemini via OpenRouter) intermittently return
        # finish_reason=error (MALFORMED_FUNCTION_CALL) on otherwise valid prompts.
        # The eval harness (tests/eval) showed certain phrasings trigger this more
        # often, so we retry a few times before giving up — the error is stochastic.
        choice = None
        msg = None
        for attempt in range(_MAX_MALFORMED_RETRIES):
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning("Agent budget: time limit reached on turn %d", turn)
                    return _STOP_MESSAGES["time_limit_reached"]
                resp = await asyncio.wait_for(
                    llm_client.chat_with_tools(
                        messages, tools, model_override=model,
                        trace_ctx=trace_ctx, span_name=f"agent_turn_{turn}",
                    ),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                logger.warning("Agent LLM call timed out on turn %d", turn)
                return _STOP_MESSAGES["time_limit_reached"]
            choice = resp.choices[0]
            msg = choice.message
            if choice.finish_reason != "error":
                break
            logger.warning(
                "Agent LLM returned finish_reason=error on turn %d (attempt %d)", turn, attempt + 1
            )
        if choice.finish_reason == "error":
            return _STOP_MESSAGES["no_final_answer"]

        # Record the model's output — always include content (even null) so
        # providers that require it in the tool-call history don't reject Turn N+1.
        msg_dict: dict = {"role": "assistant", "content": msg.content}
        if msg.tool_calls:
            msg_dict["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        messages.append(msg_dict)

        if choice.finish_reason == "stop" or (not msg.tool_calls):
            content = msg.content or ""
            if content.strip():
                quality_flags = _validate_agent_answer(content, tool_evidence, question)
                if quality_flags:
                    if trace_ctx is not None:
                        trace_ctx.set_metadata("quality_flags", quality_flags)
                    if not _grounding_retry_used and tool_evidence:
                        _grounding_retry_used = True
                        messages.pop()
                        messages.append({
                            "role": "user",
                            "content": (
                                "Переформулируй ответ: используй только числа и даты, буквально "
                                "присутствующие в результатах инструментов; используй только "
                                "Telegram HTML <b>, <i>, <code>, без Markdown."
                            ),
                        })
                        continue
                    if _capture_messages is not None:
                        _capture_messages.extend(messages)
                    return _canonical_tool_fallback(tool_evidence)
                if _capture_messages is not None:
                    _capture_messages.extend(messages)
                return content
            # Model stopped with empty content — nudge it once to produce a
            # summary (Gemini/DeepSeek via OpenRouter occasionally does this after
            # tool calls). Pop the empty assistant message first so it doesn't
            # confuse the model into repeating a blank response.
            if not _empty_answer_nudged and any(m.get("role") == "tool" for m in messages):
                logger.warning("Agent turn %d: empty stop content, injecting summary nudge", turn)
                _empty_answer_nudged = True
                if messages and messages[-1].get("role") == "assistant" and not messages[-1].get("content"):
                    messages.pop()
                messages.append({"role": "user", "content": "Сформулируй итоговый ответ на основе полученных данных."})
                continue
            if _capture_messages is not None:
                _capture_messages.extend(messages)
            return _STOP_MESSAGES["no_final_answer"]

        # Execute all tool calls in parallel (all tools are read-only)
        results = await asyncio.gather(*[
            _handle_tool_call(tc, reader, trace_ctx=trace_ctx, config=agent_config)
            for tc in msg.tool_calls
        ])
        tool_evidence.extend(results)
        tool_calls_used += len(msg.tool_calls)
        logger.info("Turn %d: %d tool calls, total so far %d", turn, len(msg.tool_calls), tool_calls_used)

        for tc, result in zip(msg.tool_calls, results):
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result[:_MAX_RESULT_CHARS],
            })

    logger.warning("Agent budget: step limit reached")
    if _capture_messages is not None:
        _capture_messages.extend(messages)
    return _STOP_MESSAGES["step_limit_reached"]


async def run_budget_agent_eval(
    question: str,
    reader: "ExcelReader",
    llm_client: "LLMClient",
    tips: list[str],
    today: str,
    default_user: str,
    model: str,
    enable_code_tool: bool = False,
    trace_ctx: "TraceContext | None" = None,
) -> tuple[str, list[dict]]:
    """Eval variant of run_budget_agent. Returns (answer, messages).

    messages contains the full conversation including all tool calls and
    tool results, so the eval harness can extract contexts and tool names.
    """
    captured: list[dict] = []
    answer = await run_budget_agent(
        question=question,
        reader=reader,
        llm_client=llm_client,
        tips=tips,
        today=today,
        default_user=default_user,
        model=model,
        enable_code_tool=enable_code_tool,
        trace_ctx=trace_ctx,
        _capture_messages=captured,
    )
    return answer, captured
