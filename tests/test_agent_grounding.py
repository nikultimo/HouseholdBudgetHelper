from llm.agent import (
    _cashflow_fast_args,
    _leave_fast_args,
    _numeric_claims,
    _canonical_tool_fallback,
    _validate_agent_answer,
)


def test_agent_answer_accepts_numbers_present_in_tool_results():
    answer = "Итого: <b>12 500 ₽</b>."
    evidence = ["Итого расходов: 12,500 ₽"]

    assert _validate_agent_answer(answer, evidence, "") == []


def test_numeric_claims_normalize_dot_and_comma_thousands():
    assert _numeric_claims("64.700 ₽") == {"64700"}
    assert _numeric_claims("64,700 ₽") == {"64700"}
    assert "29.3" in _numeric_claims("делитель 29.3")


def test_numeric_claims_treat_short_comma_as_decimal_separator():
    assert _numeric_claims("ставка 29,9%") == {"29.9"}
    assert _numeric_claims("ставка 29.9%") == {"29.9"}


def test_cashflow_fast_args_extract_named_and_current_months():
    assert _cashflow_fast_args(
        "Куда ушли деньги с марта и сколько могу откладывать сейчас?",
        "2026-07-29",
    ) == {"months": ["2026-03", "2026-07"]}


def test_leave_fast_args_extract_paid_and_unpaid_ranges():
    assert _leave_fast_args(
        "Отпуск за свой счёт с 1 по 2 июля и оплачиваемый отпуск с 10 по 19 июля",
        "2026-05-30",
    ) == {
        "vacation_start": "2026-07-10",
        "vacation_end": "2026-07-19",
        "unpaid_leave_start": "2026-07-01",
        "unpaid_leave_end": "2026-07-02",
    }


def test_agent_answer_rejects_unsupported_numeric_claim():
    flags = _validate_agent_answer(
        "Можно откладывать <b>50 000 ₽</b>.",
        ["Плановый свободный поток: 30,000 ₽"],
        "Сколько могу откладывать?",
    )

    assert "unsupported_numeric_claim" in flags


def test_agent_answer_rejects_markdown_when_html_is_required():
    flags = _validate_agent_answer(
        "* Август: 30 000 ₽",
        ["Август: 30,000 ₽"],
        "",
    )

    assert "invalid_telegram_html" in flags


def test_vacation_estimate_requires_limitations_in_final_answer():
    flags = _validate_agent_answer(
        "Отпускные (оценка): <b>10 000 ₽</b>.",
        ["Тип расчёта: прокси-оценка. Ограничения: не учтены премии."],
        "",
    )

    assert "missing_estimate_caveat" in flags


def test_canonical_tool_fallback_escapes_untrusted_text():
    result = _canonical_tool_fallback(["Итого <опасно>: 100 ₽"])

    assert "<опасно>" not in result
    assert "&lt;опасно&gt;" in result
