# Budget agent eval harness

A standalone (non-`pytest`) evaluation harness that runs the **live** tool-calling
budget agent (`llm/agent.py`) against a generated, fully generic Excel workbook with
known ground-truth values. It catches regressions in the agent's prompt, tool schemas,
and routing before deploy.

It is intentionally **not** part of the `pytest` suite because it makes real LLM calls
(and, with RAGAS, several per case). Run it manually.

## TL;DR

```bash
# Fast regression gate — deterministic scorers only (~25s, real agent calls)
python tests/eval/run_eval.py --generate --no-ragas

# Full run — adds the RAGAS LLM judge (slow, minutes)
python tests/eval/run_eval.py --generate

# Debug a subset
python tests/eval/run_eval.py --cases e001,e006

# Push items + scores to Langfuse
python tests/eval/run_eval.py --upload-dataset      # dataset items only
python tests/eval/run_eval.py --generate --upload   # run + upload scores
```

Requires `OPENROUTER_API_KEY` in the environment (no `TELEGRAM_TOKEN` / `YADISK_TOKEN`
needed). `--upload` additionally needs `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`
(and optionally `LANGFUSE_HOST`).

## How it works

```
question
   │
   ▼
run_budget_agent_eval()                 # the real agent loop, returns (answer, messages)
   │                         ╲
   ▼                          ╲
final answer            full message history (tool calls + tool results)
   │                          │
   ├──────────────┬───────────┤
   ▼              ▼           ▼
score_numeric   score_      score_tool_usage          ← deterministic, instant
score_contains  not_contains                          (no LLM)
   │
   ▼
RAGAS SingleTurnSample(response, retrieved_contexts=tool results, reference)
   │
   ▼
Faithfulness (+ FactualCorrectness when a prose reference exists)   ← LLM judge over OpenRouter
   │
   ▼
per-case table + summary  ──(optional)──▶  Langfuse dataset "budget-agent-eval"
```

1. **Generate a known-truth world.** `generate_eval_workbook.py` writes
   `eval_budget.xlsx` (gitignored) with deterministic, generic data. The correct
   answers are therefore known in advance — see `KNOWN_TOTALS`.
2. **Run the real agent** against that workbook. `run_budget_agent_eval()` is the
   production loop with one addition: it captures the full message history so the
   harness can see which tools were called and what they returned.
3. **Score on two layers** (below).
4. **Report** a per-case table and summary; optionally upload to Langfuse.

## Scoring layers

### Deterministic (instant, no LLM) — `scorers.py`

| Scorer | Checks |
|---|---|
| `score_numeric` | the ground-truth number (`reference`) appears in the answer within ±5% |
| `score_tool_usage` | every tool in `expected_tools` was actually called |
| `score_contains` | all `expected_contains` substrings are present |
| `score_not_contains` | none of `expected_not_contains` substrings are present |

### RAGAS LLM judge (slow) — `scorers.py`

| Metric | Measures | Needs |
|---|---|---|
| `Faithfulness` | every claim in the answer is grounded in the tool results (no hallucination) | answer + tool results |
| `FactualCorrectness` | answer matches the prose ground truth | `reference_answer` (prose) |

Only these two RAGAS metrics are used: both are **LLM-only**. OpenRouter does not
serve an embeddings endpoint, so embedding-based metrics (`AnswerRelevancy`,
`AnswerCorrectness`, `SemanticSimilarity`) are intentionally omitted. RAGAS's
`evaluate()` is synchronous and is run via `asyncio.to_thread`.

## Files

| File | Purpose |
|---|---|
| `generate_eval_workbook.py` | Builds `eval_budget.xlsx` (gitignored). Generic placeholder data; `KNOWN_TOTALS` documents the truth. |
| `eval_cases.py` | `EvalCase` dataclass + generic cases for query tools, leave estimates, and cash-flow advice. |
| `scorers.py` | Deterministic scorers + RAGAS metric runner. |
| `run_eval.py` | CLI runner: executes cases, scores, prints the table. |
| `langfuse_dataset.py` | Uploads dataset items and per-case scores to Langfuse. |

## Eval cases → code they guard

Each case maps to a specific failure mode; a drop in its score points at a regression
in the named location.

| Case | Question shape | Guards |
|---|---|---|
| e001, e007 | "в кофейне …" | `description_contains` vs `whose` schema; morphology fallback |
| e002 | "покажи транзакции …" | search tool + month filter |
| e003 | compound ("сколько … и покажи") | both `aggregate` and `search` called |
| e004 | "по категориям" | `category_breakdown` |
| e005 | "за апрель" | previous calendar month (`month=`) not last-30-days |
| e006 | "отдал другу" | recipient → `description_contains`, not `whose` |
| e008 | "динамика по месяцам" | `monthly_trend` |
| e009 | "за последний месяц всего" | last-30-days date range, not `month=` |
| e010 | "на транспорт" | category filter |
| e011 | "последние покупки" | not misrouted to transaction-edit; search tool |
| e012 | "какие кредиты" | `get_credits` |
| e013 | "что заплатить до зарплаты" | `get_mandatory_payments` |
| e014 | "суши бар" | multi-word `description_contains` |

## Adding a case

1. Add an `EvalCase(...)` to `EVAL_CASES` in `eval_cases.py`. Set `reference` (numeric
   ground truth), `expected_tools`, `expected_contains`, and a prose `reference_answer`
   for the LLM judge.
2. If it needs new data, add transactions/rows in `generate_eval_workbook.py` and
   update `KNOWN_TOTALS`.
3. Re-run with `--generate` so the workbook is rebuilt.

## Notes

- `eval_budget.xlsx` is regenerated by `--generate` and is gitignored — never commit it.
- All data is generic (`User1`, `Кофейня`, …); never put real names or values here.
- Results are stochastic: the model occasionally returns Gemini
  `MALFORMED_FUNCTION_CALL` (`finish_reason=error`). The agent retries this a few times
  (`_MAX_MALFORMED_RETRIES`); a single flaky failure on e006 is expected, not a regression.
- `ragas>=0.4` is in `requirements.txt`. RAGAS pulls a `langchain` 0.3.x stack as a
  transitive dependency; the bot itself does not use langchain.
