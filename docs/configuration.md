# Configuration Reference

TelegramBudgetHelper uses two config layers: secrets in `.env` and runtime settings in `config.json`. Copy from the example files and fill in real values before running.

## `.env`

Copy: `cp .env.example .env`

`.env` is gitignored — never commit it.

### Required

| Variable | Description |
|---|---|
| `TELEGRAM_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) |
| `OPENROUTER_API_KEY` | API key from [openrouter.ai](https://openrouter.ai/) |
| `YADISK_TOKEN` | Yandex Disk OAuth token |

### Optional — voice and tracing

| Variable | Default | Description |
|---|---|---|
| `WHISPER_API_KEY` | — | API key for Whisper-compatible voice transcription; voice disabled if absent |
| `WHISPER_BASE_URL` | `https://api.openai.com/v1` | Whisper endpoint (swap for a self-hosted or alternative endpoint) |
| `LANGFUSE_HOST` | — | Langfuse observability platform host |
| `LANGFUSE_PUBLIC_KEY` | — | Langfuse public key |
| `LANGFUSE_SECRET_KEY` | — | Langfuse secret key |

### Optional — file paths and logging

| Variable | Default | Description |
|---|---|---|
| `BUDGET_FILE_PATH` | `data/budget.xlsx` | Local path to the runtime Excel file |
| `LOG_LEVEL` | `INFO` | Python logging level for application logs (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `DEPS_LOG_LEVEL` | `WARNING` | Logging level for third-party dependencies (`httpx`, `telegram`, etc.). Keeps noise low and avoids accidental token leakage in debug logs. |

### Optional — Telegram network tuning

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_SEND_TIMEOUT_SEC` | `12` | Per-call timeout for `sendMessage` / `editMessageText` (guards against hangs) |
| `TELEGRAM_SEND_MAX_ATTEMPTS` | `3` | Max retry attempts for background Telegram send/edit calls |
| `TELEGRAM_READ_TIMEOUT_SEC` | `10` | HTTP read timeout for the Telegram connection |
| `TELEGRAM_WRITE_TIMEOUT_SEC` | `10` | HTTP write timeout |
| `TELEGRAM_CONNECT_TIMEOUT_SEC` | `5` | HTTP connect timeout |
| `TELEGRAM_POOL_TIMEOUT_SEC` | `1` | HTTP connection pool timeout |
| `CONCURRENT_UPDATES` | `1` | Telegram update concurrency. **Must stay `1`** — the bot writes to a single Excel file and concurrent writes corrupt it. |

## `config.json`

Copy: `cp config.example.json config.json`

`config.json` is gitignored — never commit it. The active model can be changed at runtime with the `/model` command, which persists the new value back to `config.json`.

### Model selection

| Key | Type | Description |
|---|---|---|
| `model` | string | Main LLM model ID on OpenRouter (e.g. `google/gemini-2.5-flash-lite`). Used for transaction parsing, analysis, and budget agent. |
| `router_model` | string | Model for intent classification. Can be a faster/cheaper model. Default: `google/gemini-2.5-flash-lite`. |
| `vision_model` | string | Model for photo receipt parsing. Must support vision. Default: `google/gemini-2.5-flash`. |

### User and account defaults

| Key | Type | Description |
|---|---|---|
| `default_user` | string | Name used as transaction owner (`whose` field). **Must exactly match** a `Зарплата <Name>` row key in the `⚙️ Настройки` Excel sheet. |
| `default_account` | string | Default card/account name for new transactions when not specified by the user. |
| `allowed_users` | list[int] | Required, non-empty allowlist of positive Telegram user IDs. Every update type is silently ignored unless its sender is listed; startup fails when the key is missing, empty, or malformed. For an owner-only bot, configure exactly one ID. |

### Storage and confidence

| Key | Type | Default | Description |
|---|---|---|---|
| `yadisk_path` | string | — | Full path to the Excel file on Yandex Disk (e.g. `/Budget.xlsx`). Required. |
| `backup_keep` | int | `30` | Number of local rotating backups to keep in `backups/`. |
| `confidence_threshold` | float | `0.8` | LLM confidence (0–1) below which user confirmation is requested before saving a transaction. |

### Salary model

| Key | Type | Default | Description |
|---|---|---|---|
| `salary_pay_days` | list[int] | `[5, 20]` | Two salary payment days of the month (e.g. the 5th and 20th). |
| `salary_payment_model` | string | `"working_days"` | `"working_days"` splits net salary proportionally by actual worked days from the Russian production calendar. `"fixed_percent"` uses the percentages below. |
| `salary_payment_percentages` | list[float] | — | Two values summing to `1.0`, mapped to `salary_pay_days`. Required when `salary_payment_model` is `"fixed_percent"`. |
| `vacation_average_month_days` | float | `29.3` | Divisor used by the transparent vacation-pay proxy estimate. Must be positive. |
| `vacation_ndfl_rate` | float | `0.13` | Tax-rate assumption used only to show an illustrative gross equivalent. Salary settings and the primary estimate remain net. |

Vacation estimates select the salary row matching `default_user`. If multiple salary rows exist and no member can be resolved, the bot asks for clarification instead of selecting the largest salary. The estimate does not model 12-month earnings history, bonuses, sick leave, or employer-specific payroll rules.

### Advanced / safety

| Key | Type | Default | Description |
|---|---|---|---|
| `enable_unknown_code_executor` | bool | `false` | Legacy sandboxed Python executor for unknown intents. Keep `false` in production — unknown financial questions are handled by the budget agent instead. |
