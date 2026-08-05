from tests.eval.scorers import score_numeric_grounding, score_telegram_html


def test_numeric_grounding_requires_answer_numbers_in_tool_context():
    assert score_numeric_grounding("Итого 12 500 ₽", ["Итого: 12,500 ₽"]) == 1.0
    assert score_numeric_grounding("Можно накопить 50 000 ₽", ["Поток: 30,000 ₽"]) == 0.0


def test_telegram_html_scorer_rejects_markdown_bullets():
    assert score_telegram_html("<b>Итого</b>: 100 ₽") == 1.0
    assert score_telegram_html("* Итого: 100 ₽") == 0.0
