import pytest
from datetime import date
from llm.schemas import TransactionInput


def test_transaction_input_defaults():
    txn = TransactionInput(
        date=date(2026, 4, 16),
        description="кофе",
        category="Еда и продукты",
        type="Расход",
        whose="User1",
        amount=200.0,
        account="Main Card",
        mandatory="Нет",
        confidence=0.95,
        reasoning="упомянут кофе — еда",
    )
    assert txn.whose == "User1"
    assert txn.type == "Расход"


def test_transaction_input_whose_description_distinguishes_payer_from_recipient():
    field_description = TransactionInput.model_fields["whose"].description

    assert "Who paid" in field_description
    assert "recipient in the description" in field_description


def test_transaction_input_category_description_avoids_recipient_personal_category():
    field_description = TransactionInput.model_fields["category"].description

    assert "personal category" in field_description
    assert "provided list" in field_description


def test_transaction_input_rejects_invalid_type():
    with pytest.raises(Exception):
        TransactionInput(
            date=date(2026, 4, 16),
            description="test",
            category="Еда и продукты",
            type="Неизвестно",
            whose="User1",
            amount=100.0,
            account="Main Card",
            mandatory="Нет",
            confidence=0.9,
            reasoning="test",
        )


def test_transaction_input_confidence_range():
    with pytest.raises(Exception):
        TransactionInput(
            date=date(2026, 4, 16),
            description="test",
            category="Еда и продукты",
            type="Расход",
            whose="User1",
            amount=100.0,
            account="Main Card",
            mandatory="Нет",
            confidence=1.5,
            reasoning="test",
        )


def test_build_transaction_prompt_contains_categories():
    from llm.prompts import build_transaction_prompt
    categories = ["Еда и продукты", "Бензин", "Доход"]
    prompt = build_transaction_prompt(
        text="потратил 500 на еду",
        categories=categories,
        accounts=["Main Card"],
        default_user="User1",
        today="2026-04-16",
    )
    assert "Еда и продукты" in prompt
    assert "User1" in prompt
    assert "2026-04-16" in prompt


def test_build_transaction_prompt_treats_recipient_as_description_not_whose():
    from llm.prompts import build_transaction_prompt

    prompt = build_transaction_prompt(
        text="угостил илью кофе 500",
        categories=["Еда и продукты"],
        accounts=["Main Card"],
        default_user="User1",
        today="2026-04-16",
    )

    assert "payer's expense" in prompt
    assert 'description="Угостил кофе (<Получателя>)"' in prompt
    assert "category=<purpose category>" in prompt
    assert 'whose="User1"' in prompt
    rules_text = prompt.split("Parse the following input:", 1)[0]
    assert "иль" not in rules_text.casefold()


def test_build_transaction_prompt_prefers_misc_for_unclear_recipient_expense():
    from llm.prompts import build_transaction_prompt

    prompt = build_transaction_prompt(
        text="дал кате 500",
        categories=["Никите (личное)", "Всякое"],
        accounts=["Main Card"],
        default_user="User1",
        today="2026-04-16",
    )

    assert "category=<general miscellaneous/personal category>" in prompt
    assert "Do not choose personal categories" in prompt


def test_transaction_prompt_does_not_hardcode_misc_category_when_absent():
    from llm.prompts import build_transaction_prompt

    prompt = build_transaction_prompt(
        text="дал получателю 500",
        categories=["Прочее"],
        accounts=["Main Card"],
        default_user="Алексей",
        today="2026-04-16",
    )

    assert "category=<general miscellaneous/personal category>" in prompt
    assert 'whose="Алексей"' in prompt
    assert "User1" not in prompt
    assert "User2" not in prompt
