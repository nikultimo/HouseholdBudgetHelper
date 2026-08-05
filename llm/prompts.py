from __future__ import annotations


def build_transaction_prompt(
    text: str,
    categories: list[str],
    accounts: list[str],
    default_user: str,
    today: str,
) -> str:
    cats = "\n".join(f"  - {c}" for c in categories)
    accs = "\n".join(f"  - {a}" for a in accounts)
    return f"""You are a financial assistant that parses Russian-language budget entries.

Today's date: {today}
Default user: {default_user}

Known categories:
{cats}

Known accounts:
{accs}

Rules:
- If the user does not mention a date, use today ({today}).
- If the user does not mention whose transaction, use "{default_user}".
- If the user says they paid, gave money, treated, bought, or transferred for another person,
  this is the payer's expense, not the recipient's transaction. Use "{default_user}" unless
  another payer is explicitly stated. Put the recipient in description parentheses.
- Do not choose personal categories only because a person is mentioned as recipient.
  If the purpose is unclear or is a generic personal expense for another person, choose the closest
  general miscellaneous/personal category from Known categories, not a person-specific category.
  If the purpose is explicit, choose the purpose category from Known categories.
- Recipient-expense examples use placeholders, not fixed people:
  "дал <получателю> 600 рублей" -> description="Дал 600 рублей (<Получателю>)", category=<general miscellaneous/personal category>, whose="{default_user}";
  "<получателю> на личную услугу 300" -> description="Личная услуга (<Получателю>)", category=<general miscellaneous/personal category>, whose="{default_user}";
  "угостил <получателя> кофе 500" -> description="Угостил кофе (<Получателя>)", category=<purpose category>, whose="{default_user}".
- If the user does not mention an account, use the first account in the list.
- Amount must be positive. For income, set type="Доход"; for expenses, type="Расход".
- Currency: default is RUB if not specified. If the user mentions a foreign currency
  (e.g. $50, 50 USD, €30, 30 EUR, 50 CNY, ¥500, £20 GBP), set original_currency to the
  ISO 4217 code (USD, EUR, CNY, JPY, GBP, etc.) and original_amount to the numeric value.
  Set amount equal to original_amount — the system will convert it to RUB.
  If no currency is mentioned, set original_currency="RUB" and original_amount equal to amount.
- Set confidence based on how clearly the input maps to the fields.
- Fill the reasoning field: explain each field choice in 1-2 sentences.

Examples:
Input: "кофе 200"
→ description="Кофе", type="Расход", amount=200.0, mandatory="Нет", confidence=0.95

Input: "зарплата 85000"
→ description="Зарплата", type="Доход", amount=85000.0, mandatory="Да", confidence=0.99

Input: "продукты вчера 3200"
→ description="Продукты", type="Расход", amount=3200.0, mandatory="Нет", confidence=0.92, date=<yesterday>

Parse the following input:
"{text}"
"""


def build_analysis_prompt(question: str, context: dict) -> str:
    import json
    ctx_str = json.dumps(context, ensure_ascii=False, default=str, indent=2)
    return f"""You are a personal finance assistant for a Russian-speaking household.
You have access to their budget data below. Answer the question in Russian.
Think step by step before giving your final answer.

Budget data:
{ctx_str}

Notes on the data structure:
- capital[month]["rubles"] is the actual cash balance for that month (col B of 📈 Капитал).
- capital[month]["salary_5th"] and ["salary_20th"] are the projected net salary payments on the 5th and 20th of that month, written by /update_capital.
- For forward projections use: current_capital + sum(future salary payments) - sum(expected recurring expenses).
- "salary" block gives the next individual payment amount; capital salary columns give the full multi-month schedule.

Question: {question}

Provide a clear, concise answer in Russian. Include specific numbers where relevant.
Format your answer using Telegram HTML: use <b>bold</b> for key numbers and totals, <i>italic</i> for category names. Do not use markdown (no **, no __). Keep it readable in a chat.
"""
