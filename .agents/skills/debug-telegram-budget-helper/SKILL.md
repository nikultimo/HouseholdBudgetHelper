---
name: debug-telegram-budget-helper
description: Diagnose and, when requested, fix TelegramBudgetHelper failures and wrong financial answers using focused pytest tests, live Docker Compose logs, Langfuse request traces, routing/query plans, and read-only Excel verification. Use for bug hunts, smoke tests, regressions, production diagnostics, repeated monitoring every few seconds, incorrect Telegram replies, missing or malformed traces, agent/tool failures, container errors, and post-change verification in this repository.
---

# Debug TelegramBudgetHelper

Treat one user question as an evidence chain:

`Telegram input -> router intent -> dispatcher route -> query plan or agent tools -> Excel read -> reply -> root Langfuse output`

Read the repository `AGENTS.md` before acting. Preserve its privacy, single-instance, ledger-integrity, cache-invalidation, and documentation rules. Read [references/diagnostic-map.md](references/diagnostic-map.md) for commands, expected trace fields, and symptom-to-module routing.

## Establish scope and safety

1. Run `git status -sb` and preserve unrelated work.
2. Inspect Compose state and check for a second native `bot.py` process. Never start a second bot using the same Telegram token.
3. Do not print `.env`, `config.json`, tokens, personal workbook rows, or full private traces. Report redacted summaries.
4. Treat the configured runtime workbook as the source of truth. Use reader APIs or a temporary copy for diagnosis; never mutate it merely to validate an answer.
5. Distinguish a diagnostic request from an implementation request. Explain the cause without editing code unless the user asked to fix or build.

## Build a minimal reproduction

Preserve the exact question, letter case, date context, expected meaning, actual reply, and whether `_pending` was created. Add or run the narrowest regression test that exercises the real router/dispatcher/query path. Mock only external boundaries.

For financial answers, independently calculate the expected value from the workbook contract. Check date inclusivity, income-versus-expense filtering, start-of-day balance semantics, future projection boundaries, reversal entries, currency, and rounding. Never infer correctness from a fluent answer.

## Correlate runtime evidence

Inspect the same request across all available surfaces:

1. Review bounded Docker logs around the event timestamp. Separate startup/network noise from request-specific failures.
2. Locate the matching Langfuse root trace by question and time. Retry reads with a timeout; tracing failures must not affect bot operation.
3. Verify root `input.text`, `metadata.intent`, `metadata.route`, typed `metadata.query_plan` or agent tool path, child generation/tool errors, and exact `output.reply`.
4. Compare `output.reply` byte-for-byte with the delivered Telegram text when that evidence is available.
5. Trace the first divergence backward to the owning layer. Do not patch a formatter when the query plan or workbook read is already wrong.

If Langfuse is unavailable, continue with logs, tests, and deterministic workbook checks. State which evidence is missing.

## Monitor in a bounded loop

When asked to “loop”, “watch”, or check every few seconds, default to 12 cycles at 10-second intervals unless the user specifies otherwise.

On every cycle:

1. Check container health/status.
2. Read only new or recent logs with a bounded tail.
3. Inspect new matching traces when credentials and trace identifiers are available.
4. Record new errors, changed routes, wrong/missing replies, and recoveries; avoid repeating unchanged output.
5. Run focused tests only when new evidence warrants them. Do not run the full suite every 10 seconds.

Give a short progress update at least once per minute. Stop early for a confirmed actionable failure, unsafe state, or explicit user interruption. End with cycle count, time window, evidence found, and remaining uncertainty.

## Fix the first broken layer

When fixes are authorized:

1. Add a failing regression test for the exact wording or trace invariant.
2. Make the smallest reusable fix; do not hardcode household data.
3. Update `AGENTS.md` and affected `docs/` after code changes.
4. Run focused tests, then the full suite and lint for changed Python files.
5. Rebuild Compose only when runtime code or dependencies changed. Confirm one healthy bot instance before live smoke testing.
6. Repeat the original question and re-check its root trace and exact reply when live access is authorized.

## Report evidence

Lead with the outcome. Include the reproduced question, expected versus actual behavior, first divergent layer, relevant redacted log/trace facts, tests run, and whether production was changed. Clearly label hypotheses and unavailable evidence.
