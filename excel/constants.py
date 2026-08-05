from __future__ import annotations

SHEET_TRANSACTIONS = "📋 Транзакции"
SHEET_SETTINGS = "⚙️ Настройки"
SHEET_CREDITS = "💳 Кредиты"
SHEET_PAYMENTS = "📅 Платежи"
SHEET_CAPITAL = "📈 Капитал"

LEDGER_SCHEMA_VERSION = "1"
LEDGER_COL_ENTRY_ID = 12
LEDGER_COL_POSTED_AT = 13
LEDGER_COL_KIND = 14
LEDGER_COL_REVERSES_ID = 15
LEDGER_COL_DELTA = 16
LEDGER_COL_BALANCE_AFTER = 17
LEDGER_HEADERS = (
    "entry_id",
    "posted_at",
    "entry_kind",
    "reverses_entry_id",
    "capital_delta",
    "balance_after",
)

SETTING_LEDGER_VERSION = "ledger_schema_version"
SETTING_LEDGER_OPENING_DATE = "ledger_opening_date"
SETTING_LEDGER_OPENING_BALANCE = "ledger_opening_balance"
