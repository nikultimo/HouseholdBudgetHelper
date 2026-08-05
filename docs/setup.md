# Setup Guide

Complete first-run setup for TelegramBudgetHelper.

## 1. Install dependencies

```bash
pip install -r requirements.txt
```

## 2. Create and fill `.env`

```bash
cp .env.example .env
```

### Required variables

| Variable | Description |
|---|---|
| `TELEGRAM_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) |
| `OPENROUTER_API_KEY` | API key from [openrouter.ai](https://openrouter.ai/) |
| `YADISK_TOKEN` | Yandex Disk OAuth token |

The bot will not start without all three.

### Optional variables

| Variable | Default | Description |
|---|---|---|
| `WHISPER_API_KEY` | — | API key for voice transcription (Whisper-compatible); voice disabled if absent |
| `WHISPER_BASE_URL` | `https://api.openai.com/v1` | Whisper endpoint |
| `LANGFUSE_HOST` | — | Langfuse host for LLM tracing |
| `LANGFUSE_PUBLIC_KEY` | — | Langfuse public key |
| `LANGFUSE_SECRET_KEY` | — | Langfuse secret key |
| `BUDGET_FILE_PATH` | `data/budget.xlsx` | Local path to the Excel file |
| `LOG_LEVEL` | `INFO` | Python logging level for app logs |
| `DEPS_LOG_LEVEL` | `WARNING` | Logging level for dependencies (`httpx`, `telegram`, etc.) — keeps noisy logs out and prevents accidental token leakage |
| `TELEGRAM_SEND_TIMEOUT_SEC` | `12` | Timeout for `sendMessage` / `editMessageText` calls |
| `TELEGRAM_SEND_MAX_ATTEMPTS` | `3` | Max attempts for background Telegram send/edit retries |
| `TELEGRAM_READ_TIMEOUT_SEC` | `10` | HTTP read timeout |
| `TELEGRAM_WRITE_TIMEOUT_SEC` | `10` | HTTP write timeout |
| `TELEGRAM_CONNECT_TIMEOUT_SEC` | `5` | HTTP connect timeout |
| `TELEGRAM_POOL_TIMEOUT_SEC` | `1` | HTTP pool timeout |
| `CONCURRENT_UPDATES` | `1` | Telegram update concurrency — **keep at `1`** to avoid concurrent Excel writes |

## 3. Create and fill `config.json`

```bash
cp config.example.json config.json
```

See [configuration.md](configuration.md) for the full key reference. Minimum required edits:

- `default_user` — must exactly match a `Зарплата <Name>` row key in the Excel `⚙️ Настройки` sheet.
- `allowed_users` — required non-empty list of Telegram user IDs (see [Finding your Telegram ID](#finding-your-telegram-id) below). To make the bot owner-only, list exactly your ID. The bot refuses to start if this is missing or empty.
- `yadisk_path` — full path on Yandex Disk where you will upload the Excel file (e.g. `/Budget.xlsx`).

## 4. Prepare the Excel file

Use `example/budget_example.xlsx` as a template. The bot requires exactly these five sheets with emoji-prefixed names:

| Sheet | Required content |
|---|---|
| `📋 Транзакции` | Headers in rows 1–2; transactions start at row 3 |
| `⚙️ Настройки` | Col A = key, col B = value. Must include `Зарплата <Name>` matching `default_user` |
| `💳 Кредиты` | Credit/loan rows starting at row 3 |
| `📅 Платежи` | Payment rows starting at row 2; sections split by a row containing `"ВТОРАЯ ЗАРПЛАТА"` |
| `📈 Капитал` | Col A = `YYYY-MM` month, col B = actual capital. Salary projection cols L–O written by `/update_capital` |

See [excel-schema.md](excel-schema.md) for full column-level details.

Fill in your data, then upload the file to Yandex Disk at the path set in `yadisk_path`.

On the first startup after ledger support is installed, the bot backs up and automatically migrates only the runtime workbook: it adds stable audit fields, derives a best-effort opening anchor from current capital and existing transactions, validates integrity, then uploads the migrated file. The tracked public template is not modified. Historical balances before the anchor are unavailable; older results are only as accurate as the existing journal.

**Admin setup wizard (`/setup`):** An easier alternative — configure your secrets and config, run the bot, and use the admin-only `/setup` command. It walks through setup values interactively, writes them only to `BUDGET_FILE_PATH`, and uploads the workbook to Yandex Disk. It never modifies `example/budget_example.xlsx`.

**Refreshing the public template:** Run `python3 scripts/download_example_template.py`. It downloads `/example_budget.xlsx` from Yandex Disk, validates required sheets and capital salary columns L–O, scans for obvious private values, and only then replaces `example/budget_example.xlsx`.

### Finding your Telegram ID

Message [@userinfobot](https://t.me/userinfobot) — it replies with your numeric Telegram user ID. Add it to `allowed_users` in `config.json`.

The allowlist applies before all commands, messages, photos, voice messages, setup conversations, and button callbacks. Unauthorized updates are ignored without a reply.

## 5. Run

**Development (local):**

```bash
mkdir -p data backups
python bot.py
```

**Production (recommended — Docker Compose):**

```bash
mkdir -p data backups
docker compose up -d
docker compose logs -f
```

**Important:** Never run `python bot.py` and `docker compose up` at the same time with the same `TELEGRAM_TOKEN`. Two instances cause a Telegram long-polling conflict. See [runbooks.md](runbooks.md#duplicate-process--conflict) if this happens.

## 6. Verify startup

On successful start you should see in logs:

```
Starting bot process (pid=...)
post_init: downloading budget from Yandex Disk (remote=...)
post_init: budget download complete (local=...)
Ledger migration complete: entries=... opening_date=... opening_balance=...
Bot initialized. Model: <model-id>
Application started
```

**Fallback behaviour on startup:**
- If Yandex Disk download fails but `BUDGET_FILE_PATH` exists locally → bot continues with local file and logs a warning.
- If `BUDGET_FILE_PATH` is missing → bot creates it from `example/budget_example.xlsx` after template validation so an admin can finish setup with `/setup`.
- `YadiskSync.download()` retries on transient network timeouts before failing.
