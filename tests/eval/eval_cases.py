"""Eval test cases for the budget agent.

Each case corresponds to a specific bug class found during the monitoring
session. The today field is fixed so results are reproducible.
"""
from __future__ import annotations

from dataclasses import dataclass, field

TODAY = "2026-05-30"


@dataclass
class EvalCase:
    id: str
    question: str
    today: str
    reference: str                              # ground-truth answer (numeric string or key phrase)
    expected_contains: list[str]                # strings that MUST appear in the answer
    expected_tools: list[str]                   # tool names that must be called
    expected_not_contains: list[str] = field(default_factory=list)  # must NOT appear
    reference_answer: str = ""                  # prose ground truth for RAGAS FactualCorrectness (LLM judge)
    notes: str = ""


EVAL_CASES: list[EvalCase] = [
    EvalCase(
        id="e001",
        question="Сколько потратил в кофейне за этот месяц?",
        today=TODAY,
        reference="2000",
        expected_contains=["кофейн"],   # root matches кофейня/кофейне/etc.
        expected_tools=["aggregate_transactions"],
        reference_answer="В мае 2026 в кофейне потрачено 2000 рублей.",
        notes="description_contains must be used, not whose",
    ),
    EvalCase(
        id="e002",
        question="Покажи транзакции в кофейне за май 2026",
        today=TODAY,
        reference="5",
        expected_contains=["Кофейня"],
        expected_tools=["search_transactions"],
        reference_answer="В мае 2026 было 5 транзакций в кофейне на общую сумму 2000 рублей.",
        notes="list_transactions with correct month filter; reference=5 for count",
    ),
    EvalCase(
        id="e003",
        question="Сколько в кофейне потратил за последний месяц и покажи транзакции",
        today=TODAY,
        reference="2000",
        expected_contains=["Кофейня"],
        expected_tools=["aggregate_transactions", "search_transactions"],
        reference_answer="За последний месяц в кофейне потрачено 2000 рублей по 5 транзакциям.",
        notes="Compound query — both aggregate and search tools must be called",
    ),
    EvalCase(
        id="e004",
        question="Расходы по категориям в мае 2026",
        today=TODAY,
        reference="4800",
        expected_contains=["Еда", "Транспорт", "Развлечен"],  # root for Развлечения
        expected_tools=["category_breakdown"],
        reference_answer="В мае 2026 расходы по категориям: Еда 4800, Транспорт 3500, Развлечения 3400, Здоровье 1200 рублей.",
        notes="category_breakdown tool; reference=4800 (Еда total = 2000+2800)",
    ),
    EvalCase(
        id="e005",
        question="Сколько потратил на еду за апрель 2026?",
        today=TODAY,
        reference="400",
        expected_contains=["400"],
        expected_tools=["aggregate_transactions"],
        reference_answer="В апреле 2026 на еду потрачено 400 рублей.",
        notes="Previous calendar month with month='2026-04' (not last 30 days)",
    ),
    EvalCase(
        id="e006",
        question="Сколько отдал другу за последний месяц",
        today=TODAY,
        reference="2500",
        expected_contains=["2"],   # aggregate only shows total; just check a number is present
        expected_tools=["aggregate_transactions"],
        expected_not_contains=["ничего не найдено", "нет данных"],
        reference_answer="За последний месяц другу отдано 2500 рублей.",
        notes="Recipient name → description_contains, not whose",
    ),
    EvalCase(
        id="e007",
        question="В кофейне сколько потратил за этот месяц?",
        today=TODAY,
        reference="2000",
        expected_contains=["кофейн"],
        expected_tools=["aggregate_transactions"],
        reference_answer="В этом месяце в кофейне потрачено 2000 рублей.",
        notes="Dative case 'кофейне' — morpho stem fallback must handle it",
    ),
    EvalCase(
        id="e008",
        question="Динамика расходов по месяцам",
        today=TODAY,
        reference="",
        expected_contains=["2026-04", "2026-05"],
        expected_tools=["monthly_trend"],
        reference_answer="Расходы по месяцам: апрель 2026 — 4400 рублей, май 2026 — 12900 рублей.",
        notes="monthly_trend tool; reference empty (no single numeric expected)",
    ),
    EvalCase(
        id="e009",
        question="Сколько всего потратил за последний месяц?",
        today=TODAY,
        reference="12900",
        expected_contains=["12"],   # 12,900 or 12 900 — root check
        expected_tools=["aggregate_transactions"],
        reference_answer="За последний месяц всего потрачено 12900 рублей.",
        notes="'Последний месяц' → date_from/date_to (last 30 days), not month='2026-04'",
    ),
    EvalCase(
        id="e010",
        question="Сколько потратил на транспорт за этот месяц?",
        today=TODAY,
        reference="3500",
        expected_contains=["Транспорт"],
        expected_tools=["aggregate_transactions"],
        reference_answer="В этом месяце на транспорт потрачено 3500 рублей.",
        notes="Category filter 'Транспорт'",
    ),
    EvalCase(
        id="e011",
        question="Покажи мои последние покупки",
        today=TODAY,
        reference="",
        expected_contains=["Кофейня"],
        expected_tools=["search_transactions"],
        expected_not_contains=[],
        reference_answer="Последние покупки включают транзакции в кофейне, аптеке и на бензин.",
        notes="Must call search tool and return recent transactions",
    ),
    EvalCase(
        id="e012",
        question="Какие у меня кредиты?",
        today=TODAY,
        reference="2000000",
        expected_contains=["Ипотек"],   # root matches Ипотека/ипотеку/ипотеки
        expected_tools=["get_credits"],
        reference_answer="Есть один кредит — ипотека с остатком 2000000 рублей и платежом 20000 рублей в месяц.",
        notes="get_credits tool",
    ),
    EvalCase(
        id="e013",
        question="Что нужно заплатить до зарплаты?",
        today=TODAY,
        reference="",
        expected_contains=["Интернет"],
        expected_tools=["get_mandatory_payments"],
        reference_answer="До зарплаты нужно оплатить интернет 800 рублей и телефон 500 рублей.",
        notes="payments_lookup tool",
    ),
    EvalCase(
        id="e014",
        question="Сколько стоил суши бар в мае 2026?",
        today=TODAY,
        reference="2800",
        expected_contains=["Суши"],
        expected_tools=["aggregate_transactions"],
        reference_answer="В мае 2026 в суши-баре потрачено 2800 рублей.",
        notes="Multi-word description_contains 'Суши Бар'",
    ),
    EvalCase(
        id="e015",
        question="Сколько денег будет к августу, если отпуск с 10 по 19 июля",
        today=TODAY,
        reference="",
        expected_contains=["август"],
        expected_tools=["estimate_leave_impact", "get_capital"],
        expected_not_contains=["предоставьте", "сообщите", "укажите", "предоставить"],
        reference_answer=(
            "К августу базовый прогноз капитала около 199 400 рублей. "
            "Из-за отпуска с 10 по 19 июля зарплатные выплаты за июль будут снижены. "
            "Скорректированный прогноз на август чуть ниже базового."
        ),
        notes="Vacation + capital projection — agent must call both tools and report August balance",
    ),
    EvalCase(
        id="e016",
        question="Какая будет оценка отпускных, если отпуск с 10 по 19 июля?",
        today=TODAY,
        reference="",
        expected_contains=["оценк"],
        expected_tools=["estimate_leave_impact"],
        expected_not_contains=["точно получите", "гарантированно"],
        reference_answer=(
            "Нужно дать прозрачную приблизительную оценку отпускных по текущей зарплате "
            "и сохранить оговорки инструмента."
        ),
        notes="Vacation estimate must remain explicitly approximate.",
    ),
    EvalCase(
        id="e017",
        question="Куда уходят деньги и сколько реально могу откладывать?",
        today=TODAY,
        reference="",
        expected_contains=["денежн", "расход"],
        expected_tools=["analyze_cashflow"],
        expected_not_contains=["гарантированно"],
        reference_answer=(
            "Ответ должен показать расходы по категориям и наблюдаемый денежный поток, "
            "не обещая, что весь остаток можно откладывать."
        ),
        notes="Compound advice must use cash-flow analysis instead of salary-minus-payments.",
    ),
]

EVAL_CASES_BY_ID: dict[str, EvalCase] = {c.id: c for c in EVAL_CASES}
