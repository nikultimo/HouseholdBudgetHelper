# AGENTS.md

Agent guide for TelegramBudgetHelper — a self-hosted Telegram bot for household finance tracking. Text, voice, and photo receipts become structured Excel ledger entries. Deterministic queries answer exact analytics; a tool-calling LLM agent handles unsupported analysis and advice. Backend is a single `.xlsx` file synced to Yandex Disk. Stack: Python 3.12 async, python-telegram-bot v20+, OpenRouter, openpyxl.

---

## Documentation maintenance

**After every code change:** update this file and any affected doc in `docs/`. Architecture sections, command lists, capability descriptions, and config references must stay in sync with the actual code. Never leave docs stale after a modification.

---

## Critical rules

### Product universality

Build every feature as reusable household-budget functionality. Do not hardcode personal names, Telegram user IDs, categories, accounts, salary keys, sheet row numbers for user-specific settings, or household-specific business rules. Read all such values from `config.json`, `.env`, or the Excel workbook at runtime. Tests and examples may use generic placeholders (`User1`, `Кофейня`, etc.); runtime code and LLM prompts must stay generic.

### Security & privacy

Never commit secrets, personal info, or real financial data — not in code, docs, config files, test fixtures, or commit messages. `.env` and `config.json` are gitignored; never force-add them. Example files must use only placeholder values. See [docs/security.md](docs/security.md) for the full policy including commit hygiene.

---

## Commands

```bash
pip install -r requirements.txt                                # install
pytest                                                         # all tests
pytest tests/test_excel_reader.py                             # one module
pytest tests/test_config.py::test_config_loads_defaults       # one test
python bot.py                                                  # run (dev — ensure only one instance)
docker compose up -d && docker compose logs -f                 # run (server, recommended)

# Budget agent eval harness (real LLM calls — not part of pytest)
python tests/eval/run_eval.py --generate --no-ragas            # fast deterministic
python tests/eval/run_eval.py --generate                       # + RAGAS LLM judge (slow)
python tests/eval/run_eval.py --cases e001,e006                # subset of cases
python scripts/audit_langfuse_answers.py --days 7              # redacted trace quality audit

# Refresh public Excel template from Yandex Disk
python3 scripts/download_example_template.py
```

---

## Documentation index

| Document | Contents |
|---|---|
| [docs/setup.md](docs/setup.md) | Step-by-step first-run setup: install → `.env` → `config.json` → Excel → run → verify |
| [docs/configuration.md](docs/configuration.md) | Full reference for all `.env` variables and `config.json` keys with types and defaults |
| [docs/architecture.md](docs/architecture.md) | C4 diagrams (context, container, component), request flows, module descriptions |
| [docs/excel-schema.md](docs/excel-schema.md) | Excel sheet structure, column contracts, formula conventions, template rules |
| [docs/bot-capabilities.md](docs/bot-capabilities.md) | All bot commands and capabilities; keep `/start` in sync with this |
| [docs/testing.md](docs/testing.md) | Test suite usage and eval harness guide (cases, scorers, RAGAS) |
| [docs/security.md](docs/security.md) | Security and privacy policy, commit hygiene, secret rotation |
| [docs/runbooks.md](docs/runbooks.md) | Operational runbooks: conflicts, Docker DNS, TLS timeouts |
| [docs/prd.md](docs/prd.md) | Product requirements, feature specs, and success criteria |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Dev environment setup and contribution guidelines |

---

## Architecture overview

Every Telegram update first passes a fail-closed `allowed_users` guard; unauthorized users and updates without a user are silently ignored before any command, conversation, callback, or message handler runs. Authorized messages are rate-limited (20 req/60 s), then classified by `llm/router.py` into one of 14 intents. Expense day ranges are deterministic analytics, while common vacation and cash-flow phrases execute their read-only typed tools directly; vacation/leave questions have higher precedence than balance, payment, or transaction routing. Other exact reads (including `unknown`) go through the deterministic schema-guided query service first and fall back to the tool-calling budget agent (`llm/agent.py`, max 6 turns / 15 tool calls / 45 s) only for unsupported analysis/advice. Agent answers are checked for unsupported numbers, missing leave-estimate caveats, and invalid Telegram HTML; a failed retry falls back to escaped tool evidence. Confirmed transaction history supplies category/account defaults only after at least 3 exact normalized matches with an 80% majority. Historical start-of-day capital and future start-of-month projections are separate typed operations. Confirmed transactions form an append-only Excel ledger: edit/delete create reversal entries, while `balance_after` and current capital are rebuildable views. Root Langfuse traces retain the question, intent, query/agent route, answer source, quality flags, and exact Telegram reply. All confirmed writes are backed up locally then synced to Yandex Disk. See [docs/architecture.md](docs/architecture.md) for full request flows.

---

## Key constraints

- **Cache invalidation:** `ExcelReader` caches the workbook in memory. Always call `reader.invalidate_cache()` after every write.
- **Ledger integrity:** Never physically overwrite/delete confirmed transaction rows. Use `correct_transaction()`, `reverse_transaction()`, or `reconcile_capital()` and rebuild derived balances.
- **Single instance:** `CONCURRENT_UPDATES` must stay `1` — a single Excel file cannot handle concurrent writes. Never run `python bot.py` and `docker compose up` simultaneously.
- **Access control:** `allowed_users` must be a non-empty list of positive Telegram user IDs. Configuration loading fails otherwise. A group `-1` update guard must remain ahead of every Telegram handler and silently stop unauthorized updates.
- **No formula generation:** Never hardcode participant names, account names, share setting rows, or split formulas in Python. New transaction rows copy existing derived formulas (J–K) from the workbook.
- **Runtime file separation:** The bot never modifies `example/budget_example.xlsx` at runtime. All writes go to `BUDGET_FILE_PATH`.
- **Graceful degradation:** Langfuse tracing is optional — never let tracing errors propagate. Whisper/voice is disabled when `WHISPER_API_KEY` is absent.
- **Balance semantics:** a named past/current month means historical capital at the start of day 1; a future month means projected capital at the start of day 1 using only planned flows through the preceding month. Never execute `balance_at_date` for a future date.
- **Range-query safety:** expense phrases shaped like `траты/расходы/потратил … с <day> по <day> <month>` are analytics even without an interrogative. Expense semantics take precedence over balance words such as `денег`; these requests must never enter transaction parsing or create `_pending` confirmation state.
- **Leave-query safety:** vacation, vacation-pay, and unpaid-leave questions always use the typed leave-estimate agent tool. They must never become balance, payment-checklist, or pending transaction requests merely because they mention dates or money.
- **Estimate semantics:** salary settings are net monthly values. Vacation answers are transparent proxy estimates for the configured/default member, not employer payroll calculations; preserve the limitations returned by the tool.
- **Agent grounding:** final agent numbers must occur in the question or tool evidence, and replies must use valid Telegram HTML. Do not bypass the validator for financial answers.
- **Transaction amount guard:** a router `transaction` intent without a numeric amount must request clarification and must not call the transaction parser or create `_pending` state. Never allow a zero-value confirmation caused by a missing amount.
- **Mandatory-payment-add guard:** a router `mandatory_payment_add` intent without a description, a positive amount, or a resolvable half (explicit `payment_half` or a `payment_due_day` comparable to `salary_pay_days[0]`) must request clarification and must not write to `📅 Платежи`. Only an existing empty reserved row inside the target half's block (before its `ИТОГО` row) is reused — the writer never inserts/shifts rows or rewrites the block's `SUM` formula range, since that could leave other formulas (e.g. the grand-total row) pointing at stale row numbers; a full block raises a clarification error asking for manual editing in Excel instead.
- **Payments-sheet numeric tolerance:** `get_mandatory_payments()` parses `День`/`Сумма` cells the same tolerant way as `💳 Кредиты` (numbers, comma decimals, thousands separators, ₽/руб suffixes). An amount that still can't be parsed (e.g. a formula cell with no cached value under `data_only=True`) is skipped and logged as a warning — never silently dropped without a trace, and never raised as a hard error.
- **Search clarification:** transaction search has no multi-turn pending context. If criteria are missing, ask the user to provide the complete search in one message; do not send the unsupported plan to the budget agent as if it could remember the next message.
- **Pending state:** `_pending` dict in `bot.py` is in-process only — pending transactions are lost on restart.
- **Yandex workbook downloads:** validate a downloaded workbook with `openpyxl` while it is still a temporary file; never replace a readable runtime ledger with an unreadable remote `.xlsx`.
- **Tests fixture:** `example/budget_example.xlsx` is a test fixture — do not modify its schema or add real data.
- **Code executor:** `enable_unknown_code_executor` defaults to `false`. Keep it disabled in production; unknown financial questions go to the budget agent instead.
