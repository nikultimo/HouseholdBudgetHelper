# Product Requirements Document — Telegram Budget Helper

## Overview

Telegram Budget Helper is a personal finance bot for couples or individuals who want to track expenses and income without leaving Telegram. Users write or voice natural-language messages ("coffee 200", "бензин 4500"), and the bot classifies them into structured transactions, writes them to a shared Excel file on Yandex Disk, and answers free-form questions about spending.

---

## Problem

Spreadsheet-based budgeting breaks down because manually entering transactions is slow and friction-prone. Existing finance apps require onboarding, don't work well for shared household budgets, and lock data in proprietary formats. This bot removes the friction: send a message, transaction is saved — with zero apps to install.

---

## Target Users

- **Primary:** Russian-speaking individuals or couples tracking household finances in Excel
- **Secondary:** Developers who want a self-hosted, LLM-powered personal finance bot they can run themselves or adapt

Users are assumed to be comfortable running a Python bot or Docker container and setting up a Telegram bot token.

---

## Goals

1. Let users add transactions in under 5 seconds via natural language (text or voice)
2. Keep all financial data in a user-owned Excel file — no third-party database
3. Answer analytical questions about spending from that same file
4. Remain self-hosted and low-cost to operate
5. Provide a safe first-run setup path that writes personal data only to the runtime workbook

---

## Non-Goals

- No web UI or mobile app
- No bank/card integration or automatic transaction import
- No multi-tenant or SaaS deployment (self-hosted only)
- No budgeting methodology enforcement (envelopes, zero-based, etc.)

---

## Users and Personas

### Household members

- Send expense and income messages throughout the day
- Ask questions like "how much did we spend on food this month?"
- Occasionally need to fix a wrong entry or restore a backup
- Use voice messages when typing is inconvenient

### Open-source contributor / self-hoster

- Wants to run the bot for their own household
- May need to adapt category names, accounts, or user names
- Expects clear setup instructions, a safe `/setup` wizard, a working sanitized Excel template, and tests

---

## Core Features

### 1. Natural language transaction input

Users send a free-form text message containing an amount (e.g., "кофе 200" or "bought groceries 1500"). The bot uses an LLM to parse it into a structured `TransactionInput`:

| Field | Type | Description |
|---|---|---|
| date | date | Defaults to today |
| description | string | Free-text label |
| category | enum | One of ~13 known categories |
| type | enum | `Расход` (expense) or `Доход` (income) |
| whose | string | Configured default user, explicitly mentioned payer, or shared/common owner label |
| amount | float | Positive number |
| account | string | Card/account name |
| mandatory | enum | `Да` or `Нет` |
| confidence | float | 0–1, LLM self-assessed |

When confirmed, a transaction is appended to the Excel ledger with a stable ID, signed capital delta, posting timestamp, and derived balance. Confirmed rows are never overwritten or physically deleted: corrections append a reversal and replacement; deletion appends a reversal.

When the text says the user paid, gave money, treated, bought, or transferred something for another person, the transaction belongs to the payer (`whose` remains the default user unless another payer is explicit). The recipient is kept in `description` parentheses. Personal categories are not selected just because a recipient is mentioned; unclear recipient expenses use a general miscellaneous/personal category from the categories available in Excel.

If confidence ≥ `confidence_threshold` (default 0.8): show parsed result and ask for one-tap confirmation.
If below threshold: ask a single clarifying question, then re-parse.

On confirmation: create local backup → append row to Excel → upload to Yandex Disk.

### 2. Voice message support

Voice messages (Telegram `.ogg`) are transcribed via a Whisper-compatible API endpoint, then fed into the standard transaction parsing flow.

### 3. Analytical Q&A

Read-only financial questions use a deterministic schema-guided query path before any agent fallback. Common Russian date/month phrases have a rule-based fast path. Vacation/leave wording takes precedence over balance, payments, and transactions and uses a transparent member-specific estimate. Compound spending/savings advice uses observed cash flow and category totals rather than presenting planned salary-minus-payments as guaranteed savings. Agent numbers must be grounded in tool evidence and replies must use valid Telegram HTML.

Examples:
- "Сколько потратил на еду в апреле?"
- "Когда закроем ипотеку?"
- "Сравни расходы за март и апрель"
- "Траты с 14 по 19 июня какие были"
- "Сколько было денег 14 июня"

### 4. Salary calculator

`/salary [5|20]` computes the expected net salary for the next configured payment date based on one of two config models:
- `working_days` — salary from the Excel Settings sheet split by actual worked days vs total working days, sourced from the Russian production calendar
- `fixed_percent` — net monthly salary split by `salary_payment_percentages`, mapped to `salary_pay_days`

### 5. Versioning and recovery

Before every write, a timestamped `.xlsx` backup is created locally. Up to `backup_keep` backups are retained (default: 30). `/versions` lists them; `/restore N` restores backup #N and re-uploads to Yandex Disk.

### 6. Yandex Disk sync

The Excel file lives on Yandex Disk as the source of truth. The bot downloads it on startup and uploads after every confirmed write. `/sync` forces a local re-download. `/download_excel` re-downloads the file and sends the refreshed `.xlsx` to the Telegram chat.

### 7. Multi-currency support

The LLM detects the currency of any transaction (e.g. "$50", "€200", "1000 тенге"). Non-RUB amounts are converted to RUB using live CBR (Bank of Russia) exchange rates before the transaction is saved. The original currency and amount are passed back for display; the stored `amount` is always in RUB. Rates are cached per calendar day with a fallback to the previous day's cache on network failure.

### 8. Runtime model switching

`/model <openrouter-model-id>` changes the active LLM and persists the choice to `config.json` without restarting the bot.

### 9. New-user setup

Admin-only `/setup` creates or updates the runtime workbook at `BUDGET_FILE_PATH` from the sanitized public template. It asks for setup-relevant values (user name, account, capital, salary model, payment days, mandatory payments, categories/accounts, optional import text/files/photos), shows a confirmation summary, invalidates the reader cache, and uploads the runtime workbook to Yandex Disk. Personal setup data must never be written to the tracked public template.

The public template can be refreshed only with `scripts/download_example_template.py`, which downloads `/example_budget.xlsx` from Yandex Disk and validates required sheets, capital salary columns L-O, and obvious private-data patterns before replacing `example/budget_example.xlsx`.

---

## Message Routing

```
User message
├── voice → transcribe → transaction flow
├── text with number, no question words → transaction flow
└── everything else → analysis flow

/ask <question>   → analysis flow (explicit)
/last [N]         → show last N transactions
/salary [5|20]    → salary calculation
/setup            → admin-only runtime workbook setup
/sync             → re-download from Yandex Disk
/download_excel   → re-download from Yandex Disk and send Excel to chat
/versions         → list backups
/restore N        → restore backup #N
/model <id>       → switch LLM model
/start            → welcome + command list
```

---

## Excel File Contract

The bot depends on a specific Excel schema (template: `example/budget_example.xlsx`):

| Sheet | Purpose |
|---|---|
| `📋 Транзакции` | Transaction journal — bot writes here (rows start at 3) |
| `⚙️ Настройки` | Settings: salary rows named `Зарплата <Name>`, expense shares |
| `💳 Кредиты` | Loans/credits: balance, monthly payment, rate. Numeric fields may be stored as numbers or numeric strings; rate may include `%` |
| `📅 Платежи` | Mandatory payments split at `ВТОРАЯ ЗАРПЛАТА` marker |

This schema is the integration contract between the bot and the spreadsheet. Changing sheet names or column order will break the bot.

---

## Technical Constraints

- **LLM provider:** OpenRouter (OpenAI-compatible API). Model is user-configurable; defaults to a Gemini/GPT-4o-class model.
- **Structured output:** `instructor` library for JSON-mode LLM responses.
- **Storage:** Single `.xlsx` file; no database.
- **Cloud sync:** Yandex Disk only (via REST API with httpx).
- **Tracing:** Optional Langfuse integration. The bot must function normally when Langfuse is unavailable.
- **Answer quality:** Critical numerical agent answers are validated against tool evidence; invalid synthesis retries once and falls back to canonical evidence.
- **Language:** Bot responses and prompts are in Russian. Code and docs are in English.
- **Platform:** Python 3.12+, async (python-telegram-bot v20+).

---

## Out-of-Scope Constraints

- The `whose` field is not restricted by the Pydantic schema; downstream Excel formulas/templates still need to match the configured household model.
- Excel formulas in the bundled template still encode the template's household split model. Generalizing those formulas requires an Excel-template migration.
- Pending confirmations are in-memory only — lost on restart.

---

## Success Criteria

- A contributor can clone the repo, configure `.env` and `config.json`, and have a working bot in under 15 minutes
- A transaction can be added end-to-end (text → LLM → confirmation → Excel → Yandex Disk) without errors
- All tests pass (`pytest`) with no external services required (mocked)
- The bot handles Langfuse/Yandex Disk unavailability without crashing
- Vacation estimates select the intended household member, do not double-tax net salary, and disclose their limitations
- Every completed message path records a route and exact reply when Langfuse is configured
