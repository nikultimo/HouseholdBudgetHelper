# Excel Schema

The bot depends on a specific Excel workbook schema. The public template is `example/budget_example.xlsx`.

## Required sheets

All five sheets must exist with exact emoji-prefixed names. The optional `📊 Сводка` sheet may remain for manual spreadsheet use; runtime code must treat it as absent.

### `📋 Транзакции` — Transaction journal

| Column | Content |
|---|---|
| A | Date |
| B | Static `"YYYY-MM"` string (month label, written by the bot) |
| C | Description |
| D | Category |
| E | Type (`Расход` / `Доход`) |
| F | Whose (configured user name or explicitly mentioned payer) |
| G | Amount (always in RUB) |
| H | Account |
| I | Mandatory (`Да` / `Нет`) |
| J–K | Derived formulas copied from the nearest previous formula row |
| L | Stable `entry_id` (UUID hex) |
| M | UTC posting timestamp (`posted_at`) |
| N | `entry_kind`: `transaction`, `reversal`, or `adjustment` |
| O | `reverses_entry_id` for reversal rows |
| P | Signed `capital_delta` (`Доход` positive, `Расход` negative) |
| Q | Derived `balance_after`; rebuildable from the opening anchor and deltas |

Transactions start at **row 3**; rows 1–2 are headers. Runtime workbooks are migrated automatically to ledger schema v1; the tracked public fixture stays in its legacy shape. Confirmed rows are append-only: corrections use a reversal plus replacement, and deletion uses a reversal. Normal analytics hides reversed/audit rows.

### `⚙️ Настройки` — Settings

| Column | Content |
|---|---|
| A | Setting key |
| B | Setting value |

Salary rows must be named `Зарплата <Name>`. The `default_user` in `config.json` must exactly match the `<Name>` part. The bot uses the only positive `Зарплата ...` key as a fallback when there is exactly one.

Other typical keys: account names, category lists, expense-share configuration.

Runtime migration also maintains `ledger_schema_version`, `ledger_opening_date`, and `ledger_opening_balance`. These generic keys anchor historical start-of-day balance calculations.

### `💳 Кредиты` — Credits/loans

| Column | Content |
|---|---|
| A | Name / description |
| B | Type |
| C | Balance |
| D | Monthly payment |
| E | Interest rate |

Credit rows start at **row 3**. Numeric fields tolerate spreadsheet strings like `29.9%`, `29,9%`, and values with spaces.

### `📅 Платежи` — Mandatory payments

| Column | Content |
|---|---|
| A | Payment name |
| B | Due day of month |
| C | Amount |

Rows start at **row 2**. The sheet is split into two sections by a row containing `"ВТОРАЯ ЗАРПЛАТА"` or `"2️⃣"` — this marks payments due after the second salary.

### `📈 Капитал` — Capital projection

| Column | Content |
|---|---|
| A | Month (`YYYY-MM`) |
| B | Materialized aggregate RUB balance for the current month; rebuilt from the ledger |
| L–O | Salary projections written by `/update_capital` |

Month rows start at **row 3** (row 2 is the header). The bot uses only the current month's col B as actual capital. `/capital_update`-style natural-language updates create an auditable adjustment instead of overwriting history. Historical queries are reconstructed from the ledger anchor and deltas. A future start-of-month projection uses current materialized net assets as its anchor, then adds L–M salary and subtracts mandatory payments for complete intervening months only. For example, the value at the start of December includes August–November flows when July is current; it excludes both already-materialized July and not-yet-started December flows.

## Template rules

- The public template `example/budget_example.xlsx` must always contain all five required sheets.
- It is a sanitized workbook with generic sample data only — no real names, amounts, or account details.
- The bot **never** modifies `example/budget_example.xlsx` at runtime. All runtime writes go to `BUDGET_FILE_PATH` (`data/budget.xlsx` by default).
- To refresh the tracked template from Yandex Disk: `python3 scripts/download_example_template.py` — it validates required sheets, capital salary columns L–O, and obvious private-value patterns before replacing the file.
- Tests use `example/budget_example.xlsx` as a fixture. Do not modify its schema.

## Excel formula contract

The bot copies J–K formulas from the nearest previous formula row using translated row references. It does not generate household-split formulas in Python. If you need to change the split model, modify the formulas in the Excel template/workbook — the bot will copy the updated formulas going forward.
