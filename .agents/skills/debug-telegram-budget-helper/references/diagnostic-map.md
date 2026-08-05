# TelegramBudgetHelper diagnostic map

## Safe command set

Run from the repository root. Prefer bounded output.

```bash
git status -sb
docker compose config --services
docker compose ps
ps aux | grep -E "[p]ython(3)? .*bot.py" || true
docker compose logs --since 2m --tail 300 --no-color budget-bot
pytest tests/test_dispatcher.py tests/test_query_handler.py tests/test_routing.py -q
pytest tests/test_request_trace.py tests/test_tracing.py -q
pytest
ruff check <changed-python-files>
docker compose up -d --build
```

If `pytest` or `ruff` is unavailable on the host, use the project image instead of modifying the host environment:

```bash
docker compose build budget-bot
docker compose run --rm --no-deps -T budget-bot pytest -q
docker compose run --rm --no-deps -T budget-bot ruff check <changed-python-files>
```

When Ruff is not installed in the project image either, install it only in that disposable `docker compose run` container or report lint as unavailable. Do not add a runtime dependency solely for diagnostics.

Do not use `docker compose logs -f` inside a bounded diagnostic pass. Do not run `python bot.py` while Compose owns the bot.

## Request evidence contract

For an exact financial question, the root Langfuse trace should provide:

| Evidence | Expected value |
|---|---|
| Root input | `input.text` equals the original question |
| Classification | `metadata.intent` reflects the effective intent |
| Execution | `metadata.route` identifies deterministic, capital, clarification, or agent handling |
| Deterministic read | Typed `metadata.query_plan` records operation and normalized arguments |
| Agent read | Child generations/tool calls show selected tools, arguments, results, and failures |
| Delivered result | `output.reply` equals the complete Telegram reply |

Use `llm.tracing.read_trace_for_diagnostics(trace_id, ...)` for a known trace ID. It has bounded retries and read timeout and returns `None` on failure. Never paste credentials into commands or output. If listing traces through the Langfuse API is necessary, load credentials from the existing process environment and print only IDs, timestamps, routing metadata, and redacted question/reply summaries.

## First-divergence map

| Symptom | Inspect first | Then inspect |
|---|---|---|
| Expense range becomes pending transaction | `llm/router.py`, `dispatcher.py` | `tests/test_routing.py`, `tests/test_dispatcher.py` |
| Exact question falls into advice agent | `llm/query_planner.py`, `dispatcher.py` | agent fallback metadata |
| Historical balance is wrong | `handlers/query.py`, `excel/reader.py` | ledger anchors/reversals in `excel/ledger.py` |
| Future balance uses historical operation | query plan operation and date normalization | projection logic and planned-flow boundary |
| Correct computation, wrong Telegram text | handler formatter, `telegram_helpers.py` | root `output.reply` |
| Reply is delivered but root output is empty | shared reply helper, `llm/tracing.py` | premature `finish()` call |
| Root output differs from Telegram | multiple reply paths or post-send edits | callback/edit handlers |
| Missing child tool details | `llm/client.py`, `llm/agent.py` trace context | Langfuse flush/SDK errors |
| Duplicate replies or polling conflict | Compose status and native process list | Telegram `Conflict` log entries |
| Workbook changes are not visible | writer cache invalidation | Yandex sync result and runtime file path |

## Answer-correctness checks

- Expense ranges include both boundary dates and exclude income.
- A named past/current month means the start of day 1.
- A future named month or “к <month>” means projected capital at the start of day 1 using planned flows only through the prior month.
- Historical `balance_at_date` is never used for a future date.
- Confirmed edit/delete operations are reversals, not physical row mutation.
- Current and historical balances account for the opening anchor and rebuildable derived balances.
- The displayed total agrees with the listed transactions, currency conversion, and rounding rules.

## Validation ladder

Run the cheapest relevant level first:

1. One regression test or one test module.
2. Routing/query/trace cluster for cross-layer changes.
3. Full `pytest`.
4. `ruff check` on changed Python files.
5. Compose rebuild and health/log check for runtime changes.
6. Live Telegram smoke plus matching Langfuse trace only when live interaction is in scope.

Record skipped levels and why. A passing unit test does not prove a live trace or delivered reply is correct.
