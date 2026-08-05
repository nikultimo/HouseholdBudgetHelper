import json
import os
import pathlib
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

_CONFIG_DIR = pathlib.Path(__file__).parent

@dataclass
class Config:
    model: str
    default_user: str
    default_account: str
    backup_keep: int
    confidence_threshold: float
    yadisk_path: str
    router_model: str
    vision_model: str
    telegram_token: str
    openrouter_api_key: str
    yadisk_token: str
    langfuse_host: str
    langfuse_public_key: str
    langfuse_secret_key: str
    whisper_api_key: str
    whisper_base_url: str
    budget_file_path: str
    allowed_users: list[int]
    salary_pay_days: tuple[int, int]
    salary_payment_model: str
    salary_payment_percentages: tuple[float, float]
    vacation_average_month_days: float
    vacation_ndfl_rate: float
    enable_unknown_code_executor: bool

    def update_model(self, model: str, config_path: str | None = None) -> None:
        self.model = model
        path = pathlib.Path(config_path) if config_path else _CONFIG_DIR / "config.json"
        with open(path) as f:
            data = json.load(f)
        data["model"] = model
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def _load_pay_days(data: dict) -> tuple[int, int]:
    days = data.get("salary_pay_days", [5, 20])
    if len(days) != 2:
        raise ValueError(f"salary_pay_days must have exactly 2 elements, got {days}")
    lo, hi = sorted(int(d) for d in days)
    return (lo, hi)


def _load_salary_payment_model(data: dict) -> str:
    model = str(data.get("salary_payment_model", "working_days")).strip()
    if model not in {"working_days", "fixed_percent"}:
        raise ValueError(
            "salary_payment_model must be 'working_days' or 'fixed_percent', "
            f"got {model!r}"
        )
    return model


def _load_salary_payment_percentages(data: dict) -> tuple[float, float]:
    values = data.get("salary_payment_percentages", [0.5, 0.5])
    if len(values) != 2:
        raise ValueError(
            f"salary_payment_percentages must have exactly 2 elements, got {values}"
        )
    first, second = (float(values[0]), float(values[1]))
    if first < 0 or second < 0:
        raise ValueError("salary_payment_percentages must be non-negative")
    if abs((first + second) - 1.0) > 0.0001:
        raise ValueError("salary_payment_percentages must sum to 1.0")
    return (first, second)


def _load_vacation_estimate(data: dict) -> tuple[float, float]:
    average_days = float(data.get("vacation_average_month_days", 29.3))
    ndfl_rate = float(data.get("vacation_ndfl_rate", 0.13))
    if average_days <= 0:
        raise ValueError("vacation_average_month_days must be positive")
    if not 0 <= ndfl_rate < 1:
        raise ValueError("vacation_ndfl_rate must be in [0, 1)")
    return average_days, ndfl_rate


def _load_allowed_users(data: dict) -> list[int]:
    users = data.get("allowed_users")
    if (
        not isinstance(users, list)
        or not users
        or any(isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0 for user_id in users)
    ):
        raise ValueError(
            "allowed_users must be a non-empty list of positive Telegram user IDs"
        )
    return list(dict.fromkeys(users))


def load_config(config_path: str | None = None) -> Config:
    path = pathlib.Path(config_path) if config_path else _CONFIG_DIR / "config.json"
    with open(path) as f:
        data = json.load(f)
    vacation_average_month_days, vacation_ndfl_rate = _load_vacation_estimate(data)
    return Config(
        model=data["model"],
        default_user=data["default_user"],
        default_account=data["default_account"],
        backup_keep=data["backup_keep"],
        confidence_threshold=data["confidence_threshold"],
        yadisk_path=data["yadisk_path"],
        router_model=data.get("router_model", "google/gemini-2.5-flash-lite"),
        vision_model=data.get("vision_model", "google/gemini-2.5-flash"),
        telegram_token=os.environ["TELEGRAM_TOKEN"],
        openrouter_api_key=os.environ["OPENROUTER_API_KEY"],
        yadisk_token=os.environ["YADISK_TOKEN"],
        langfuse_host=os.environ.get("LANGFUSE_HOST", ""),
        langfuse_public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
        langfuse_secret_key=os.environ.get("LANGFUSE_SECRET_KEY", ""),
        whisper_api_key=os.environ.get("WHISPER_API_KEY", ""),
        whisper_base_url=os.environ.get("WHISPER_BASE_URL", "https://api.openai.com/v1"),
        budget_file_path=os.environ.get("BUDGET_FILE_PATH", "data/budget.xlsx"),
        allowed_users=_load_allowed_users(data),
        salary_pay_days=_load_pay_days(data),
        salary_payment_model=_load_salary_payment_model(data),
        salary_payment_percentages=_load_salary_payment_percentages(data),
        vacation_average_month_days=vacation_average_month_days,
        vacation_ndfl_rate=vacation_ndfl_rate,
        enable_unknown_code_executor=bool(data.get("enable_unknown_code_executor", False)),
    )
