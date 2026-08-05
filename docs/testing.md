# Testing

## Unit and integration tests

All tests are in `tests/` and use `pytest` + `pytest-asyncio`. All external dependencies (LLM calls, Yandex Disk, Langfuse) are mocked — no real API tokens needed to run the suite.

```bash
pytest                                                          # all tests
pytest tests/test_excel_reader.py                              # one module
pytest tests/test_config.py::test_config_loads_defaults        # one test
pytest tests/test_ledger.py                                   # migration/audit/balance history
```

`example/budget_example.xlsx` is used as a fixture for reader/writer tests. Do not modify its schema.

`tests/test_ledger.py` creates temporary runtime workbooks and covers idempotent migration, opening-anchor math, start-of-day boundaries, backdated entries, reversal-based correction, and deletion. Query/routing tests cover expense-range precedence, leave-query precedence, missing transaction amounts, inclusive boundaries, income exclusion, named-month semantics, and future projections. Agent tests cover member-specific net vacation estimates, unpaid-leave tooling, cash-flow wording, numeric grounding, Telegram HTML, and canonical fallback. Transaction tests cover confirmed-history defaults. Trace tests verify intent, plan, route, answer source, and exact `output.reply`.

## Budget agent eval harness (`tests/eval/`)

A standalone evaluation harness that exercises the live tool-calling agent against a synthetic, fully generic workbook with known ground-truth values. It is **not** part of `pytest` — it makes real LLM calls.

**Prerequisites:** `OPENROUTER_API_KEY` must be set. For `--upload`, also set `LANGFUSE_*` variables.

### Files

| File | Purpose |
|---|---|
| `generate_eval_workbook.py` | Builds `tests/eval/eval_budget.xlsx` (gitignored) via `openpyxl`. All data uses generic placeholders (`User1`, `Кофейня`, …). `KNOWN_TOTALS` documents ground-truth sums. |
| `eval_cases.py` | `EvalCase` dataclass + generic analytics, vacation, and cash-flow cases. Each case has numeric/text/tool expectations and a prose answer for the RAGAS judge. |
| `scorers.py` | **Deterministic** (instant, no LLM): `score_numeric`, `score_contains`, `score_not_contains`, `score_tool_usage`. **RAGAS LLM judge**: `Faithfulness` (always) + `FactualCorrectness` (when `reference_answer` is set). No embedding metrics — OpenRouter has no embeddings endpoint. |
| `run_eval.py` | CLI runner. Calls `run_budget_agent_eval()`, extracts tool results as RAGAS contexts, scores, and prints a per-case table + summary. |
| `langfuse_dataset.py` | Pushes dataset items and per-case scores to the Langfuse dataset `budget-agent-eval`. |

### Usage

```bash
# Generate the eval workbook and run deterministic scorers only (fast)
python tests/eval/run_eval.py --generate --no-ragas

# Full run with RAGAS LLM judge (slow — makes extra LLM calls)
python tests/eval/run_eval.py --generate

# Run a subset of cases
python tests/eval/run_eval.py --cases e001,e006

# Upload scores to Langfuse
python tests/eval/run_eval.py --generate --upload

# Upload dataset items to Langfuse
python tests/eval/run_eval.py --generate --upload-dataset
```

### Scorer layers

1. **Deterministic (instant):** numeric/reference, contains/forbidden text, tool usage, numeric grounding against tool evidence, and valid Telegram HTML.
2. **RAGAS LLM judge (slow):** `Faithfulness` verifies the answer is grounded in tool results. `FactualCorrectness` compares against `reference_answer` when present. Both run via OpenRouter — embedding-based metrics (`AnswerRelevancy`, `AnswerCorrectness`) are intentionally excluded.

`ragas>=0.4` is in `requirements.txt`. The `evaluate()` call is synchronous and run via `asyncio.to_thread`.

## Redacted production-trace audit

```bash
python scripts/audit_langfuse_answers.py --days 7
```

The command is read-only. It reports trace IDs and flags for missing routes/replies, expense-as-balance plans, leave-route violations, and Markdown in HTML replies without printing private message or financial content.
