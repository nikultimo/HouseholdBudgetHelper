from datetime import date as _date
from typing import Literal
from pydantic import BaseModel, Field


class TransactionInput(BaseModel):
    date: _date = Field(description="Transaction date. Default to today if not mentioned.")
    description: str = Field(description="Short human-readable description of the expense/income.")
    category: str = Field(
        description=(
            "Category from the provided list. Pick the closest match. Do not choose a personal "
            "category only because a person is mentioned as the recipient; for unclear gifts, "
            "transfers, or personal expenses for another person, prefer a general miscellaneous/"
            "personal category from the provided list."
        )
    )
    type: Literal["Расход", "Доход"] = Field(description="'Расход' for expense, 'Доход' for income.")
    whose: str = Field(
        description=(
            "Who paid or made the transaction. Use the configured default user when the payer is "
            "not explicitly mentioned. A person who receives money, a gift, or a treat is not the "
            "owner; keep that recipient in the description instead."
        ),
    )
    original_currency: str = Field(
        default="RUB",
        description=(
            "ISO 4217 currency code as mentioned by the user (e.g. RUB, USD, EUR, CNY, GBP). "
            "Default: RUB if the user does not specify a currency."
        ),
    )
    original_amount: float | None = Field(
        default=None,
        description=(
            "Amount in the original currency as mentioned by the user. "
            "Set to the same value as amount when the currency is RUB."
        ),
    )
    amount: float = Field(
        gt=0,
        description=(
            "Amount in rubles (RUB), positive number. "
            "If the currency is not RUB, set this equal to original_amount — the system will convert it."
        ),
    )
    account: str = Field(
        description="Bank account used. Use the first configured account when the account is not mentioned.",
    )
    mandatory: Literal["Да", "Нет"] = Field(
        description="'Да' if this is a recurring/mandatory payment, 'Нет' otherwise."
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Your confidence in this parse, 0.0–1.0.",
    )
    reasoning: str = Field(
        description="Brief chain-of-thought: explain why you chose each field value."
    )
