# Bot Capabilities

All capabilities exposed to users. The `/start` command in `bot.py` (`cmd_start` handler) is the user-facing summary — **update it whenever a feature is added or removed**.

## Capabilities

### Транзакции (Transactions)

Text, voice (Whisper transcription), and photo receipt → structured `TransactionInput` → inline confirm keyboard → Excel append + Yandex Disk upload.

Text transactions require a numeric amount. If a message is classified as a transaction but contains no amount, the bot asks for one and does not call the parser or create a pending confirmation.

- LLM parses natural-language messages into: date, description, category, type, whose, amount, account, mandatory.
- Confidence below `confidence_threshold` triggers a user confirmation request.
- Non-RUB amounts are converted to RUB using live CBR exchange rates; the original currency and amount are shown for review.
- Payments/gifts/treats for another person stay on the payer (`whose` = payer). The recipient goes in `description` parentheses: `Угостил кофе (<Получателя>)`. Personal categories are not inferred solely from a recipient mention.
- After parsing, an exact normalized description can reuse a category/account from confirmed history only when at least 3 matches exist and one value has an 80% majority.

### Поиск транзакций (Search)

Natural-language search across all transactions by description, category, or month. Results are shown newest-first, up to 20 results with an expense total. Search criteria must be supplied in one message (for example, “найди транзакцию: билет”); the bot explicitly requests this form when a bare “найди транзакцию” has no usable filter.

### Редактирование/удаление транзакций (Edit/Delete)

Shows last 5 transactions newest-first as an inline keyboard.

**Edit:** Select transaction → edit the in-memory draft → `✅ Сохранить` appends a reversal of the original and a corrected replacement. Stable ledger IDs are used instead of mutable Excel row numbers.

**Delete:** Select transaction → confirm → append a reversal. The original audit row remains in Excel but disappears from normal transaction views.

### Аналитика (Analytics)

Exact budget reads, including router `unknown` results, run through the deterministic schema-guided query service first. Expense day ranges such as “траты с 14 по 19 июня” and “сколько потратил денег с 10 по 18 июня” are recognized as analytics; expense wording takes precedence over the incidental word “денег”. Router and dispatcher protections prevent these requests from creating a pending transaction. The service supports aggregates/lists, inclusive expense-period summaries, comparisons, historical capital at the start of a day, and projected capital at the start of a future month. Unsupported analysis and advice falls back to the tool-calling agent (max 6 turns, 15 tool calls, 45 s):

- `search_transactions` — filter by date/category/description
- `aggregate_transactions` — sum/count by period
- `analyze_cashflow` — category totals and observed income-minus-expense flow; never labels the result guaranteed savings
- `category_breakdown` — expense breakdown by category
- `monthly_trend` — month-over-month spending
- `get_capital` — current and projected capital
- `get_salary` — salary from settings
- `get_mandatory_payments` — payments checklist data
- `get_credits` — credit/loan summary
- `estimate_leave_impact` — transparent paid/unpaid leave estimate for the configured member, with affected salary dates and explicit limitations
- `run_python_code` (optional, disabled by default) — sandboxed custom calculations

### Отпускные (Vacation pay)

Vacation wording has precedence over balance, payment-checklist, and transaction routing. Common same-month paid-vacation ranges execute `estimate_leave_impact` directly; one named unpaid-leave period and an optional capital read are included when present. The tool uses the selected member's current net monthly salary as a proxy, excludes public holidays, and shows affected salary dates. It is explicitly an estimate—not an employer payroll calculation—and discloses that 12-month earnings history, bonuses, sick leave, and employer rules are not modeled.

Agent synthesis is checked against tool evidence. Unsupported numbers or invalid Telegram HTML trigger one constrained retry and then a canonical tool-evidence fallback.

### Зарплата (Salary)

Query or update salary setting. `/salary [5|20]` computes the expected net salary for the next configured payment date (5th or 20th) using either:
- `working_days` — proportional to worked days vs total working days from the Russian production calendar
- `fixed_percent` — split by configured `salary_payment_percentages`

### Капитал (Capital)

Query current capital or ask for capital at a named date/month. “Баланс в июне” means the historical ledger balance at the start of 1 June when June is past/current. A future month, including “к декабрю”, means a projection at the start of day 1; it starts from current materialized net capital and applies planned salaries minus mandatory payments only through the end of the previous month. Historical `balance_at_date` is never used for future dates. Capital reconciliation creates an audit adjustment. `/update_capital` rebuilds salary projection columns L–O for the current month plus 6 months ahead.

### Платежи (Payments)

Ask "what do I need to pay?" → ☐ checklist from `📅 Платежи` sheet.

### Setup

Admin-only `/setup` creates or updates the runtime workbook at `BUDGET_FILE_PATH` and uploads it to Yandex Disk. Never modifies `example/budget_example.xlsx`.

### Статистика (Stats)

`/stats [YYYY-MM]` — expense breakdown by category for a given month.

### Подсказки агента (Agent tips)

`/tip <текст>` appends a persistent formatting or behaviour hint to `data/agent_tips.txt`. The budget agent reads and applies all saved tips on every invocation. Format reference: `agent_tips.example.txt`.

### Админ-синхронизация (Admin sync)

- `/sync` — re-downloads the Excel file from Yandex Disk, invalidates the reader cache. Reports success only after download completes.
- `/download_excel` — re-downloads and sends the refreshed `.xlsx` to the chat.
- `/last [N]` — shows the last N transactions.
- `/model <id>` — switches the active LLM model and persists to `config.json`.
- `/versions` — lists local backups.
- `/restore N` — restores backup #N and re-uploads to Yandex Disk.

## Command reference

```
/start            Welcome message + capability summary
/salary [5|20]    Net salary for next payment date
/update_capital   Rebuild capital projection columns
/stats [YYYY-MM]  Expense breakdown by category
/tip <text>       Append agent behaviour hint
/setup            Admin: create/update runtime workbook
/sync             Admin: re-download Excel from Yandex Disk
/download_excel   Admin: re-download and send Excel to chat
/last [N]         Admin: show last N transactions
/model <id>       Admin: switch LLM model
/versions         Admin: list local backups
/restore N        Admin: restore backup #N
```

## Rate limiting

20 requests per 60 seconds per user, enforced in `bot.py:_check_rate_limit()`.
