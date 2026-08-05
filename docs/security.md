# Security & Privacy Policy

## What must never appear in tracked files or git history

| Category | Examples |
|---|---|
| **Secrets** | API keys, tokens, passwords, OAuth credentials |
| **Personal info** | Real names, email addresses, Telegram user IDs, phone numbers |
| **Infrastructure** | Server hostnames, IP addresses, VPS identifiers, internal URLs |
| **Real financial data** | Actual account names, real transaction descriptions, real salary figures |

This applies to: code, docs, config files, test fixtures, commit messages, and PR descriptions.

## Gitignored files

`.env` and `config.json` are gitignored — **never force-add them** (`git add -f`).

Example files must contain only placeholder values:
- `.env.example` → `your_token_here`, `123456789`, etc.
- `config.example.json` → placeholder strings and generic numbers
- `example/budget_example.xlsx` → generic sample data only (`User1`, `Кофейня`, etc.)

## Commit hygiene

Before committing, scan staged changes for real values:

```bash
git diff --staged | grep -iE "(token|key|password|@gmail|@mail)"
```

Abort the commit if anything real appears.

Git commit author must use the generic repo identity — not a personal email or server hostname:

```
TelegramBudgetHelper Maintainer <maintainer@example.com>
```

## Generic code requirement

Runtime code and LLM prompts must stay generic. Do not hardcode:
- Personal names or Telegram user IDs
- Household-specific categories, accounts, or salary keys
- Sheet row numbers for user-specific settings
- Business rules that apply only to one household

All such values must come from `config.json`, `.env`, or the Excel workbook at runtime.

## Telegram access control

`config.json` must contain a non-empty `allowed_users` list of positive Telegram user IDs. Use a single ID for an owner-only deployment. Configuration loading fails closed if the key is missing, empty, or malformed, and a global pre-handler silently drops every unauthorized update—including commands, callback buttons, files, photos, and setup-conversation messages—before business logic runs.

Telegram still transports messages to the bot account; application-level access control ensures the bot does not process or answer them. Keep the bot token secret and rotate it with BotFather if it may have been exposed.

## If a secret is accidentally committed

1. Treat the secret as **compromised immediately** — rotate it before doing anything else.
2. Rewrite git history to remove it (`git filter-branch` or BFG Repo Cleaner).
3. Force-push the cleaned history and notify all collaborators to re-clone.

## Langfuse and tracing

Langfuse tracing is optional. When enabled, LLM traces (prompts and completions) are sent to the configured Langfuse host. Ensure the Langfuse instance does not receive real personal data from users. The `DEPS_LOG_LEVEL=WARNING` setting prevents dependency debug logs from accidentally logging full HTTP request/response bodies containing tokens.
