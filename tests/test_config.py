import json
import os
import pytest
from unittest.mock import patch


def test_config_loads_defaults(tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "model": "anthropic/claude-sonnet-4-5",
        "default_user": "User1",
        "default_account": "Main Card",
        "backup_keep": 30,
        "confidence_threshold": 0.8,
        "yadisk_path": "/budget/Budget.xlsx",
        "allowed_users": [123456789],
    }))
    env = {
        "TELEGRAM_TOKEN": "123:ABC",
        "OPENROUTER_API_KEY": "or-key",
        "YADISK_TOKEN": "yd-token",
        "LANGFUSE_HOST": "http://localhost:3000",
        "LANGFUSE_PUBLIC_KEY": "pk",
        "LANGFUSE_SECRET_KEY": "sk",
        "WHISPER_API_KEY": "",
        "WHISPER_BASE_URL": "https://api.openai.com/v1",
        "BUDGET_FILE_PATH": str(tmp_path / "data" / "budget.xlsx"),
    }
    with patch.dict(os.environ, env, clear=True):
        from config import load_config
        cfg = load_config(str(cfg_file))

    assert cfg.model == "anthropic/claude-sonnet-4-5"
    assert cfg.default_user == "User1"
    assert cfg.telegram_token == "123:ABC"
    assert cfg.backup_keep == 30
    assert cfg.whisper_base_url == "https://api.openai.com/v1"
    assert cfg.whisper_api_key == ""
    assert cfg.confidence_threshold == 0.8
    assert cfg.openrouter_api_key == "or-key"
    assert cfg.salary_pay_days == (5, 20)
    assert cfg.salary_payment_model == "working_days"
    assert cfg.salary_payment_percentages == (0.5, 0.5)
    assert cfg.vacation_average_month_days == 29.3
    assert cfg.vacation_ndfl_rate == 0.13
    assert cfg.enable_unknown_code_executor is False
    assert cfg.allowed_users == [123456789]


def test_config_loads_fixed_salary_percentages(tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "model": "anthropic/claude-sonnet-4-5",
        "default_user": "User1",
        "default_account": "Main Card",
        "backup_keep": 30,
        "confidence_threshold": 0.8,
        "yadisk_path": "/budget/Budget.xlsx",
        "allowed_users": [123456789],
        "salary_payment_model": "fixed_percent",
        "salary_payment_percentages": [0.4, 0.6],
    }))
    env = {
        "TELEGRAM_TOKEN": "123:ABC",
        "OPENROUTER_API_KEY": "or-key",
        "YADISK_TOKEN": "yd-token",
    }
    with patch.dict(os.environ, env, clear=True):
        from config import load_config
        cfg = load_config(str(cfg_file))

    assert cfg.salary_payment_model == "fixed_percent"
    assert cfg.salary_payment_percentages == (0.4, 0.6)


def test_config_rejects_invalid_fixed_salary_percentages(tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "model": "anthropic/claude-sonnet-4-5",
        "default_user": "User1",
        "default_account": "Main Card",
        "backup_keep": 30,
        "confidence_threshold": 0.8,
        "yadisk_path": "/budget/Budget.xlsx",
        "allowed_users": [123456789],
        "salary_payment_model": "fixed_percent",
        "salary_payment_percentages": [0.4, 0.4],
    }))
    env = {
        "TELEGRAM_TOKEN": "123:ABC",
        "OPENROUTER_API_KEY": "or-key",
        "YADISK_TOKEN": "yd-token",
    }
    with patch.dict(os.environ, env, clear=True):
        from config import load_config
        with pytest.raises(ValueError):
            load_config(str(cfg_file))


@pytest.mark.parametrize("allowed_users", [None, [], ["123"], [0], [-1]])
def test_config_rejects_unsafe_allowed_users(tmp_path, allowed_users):
    data = {
        "model": "anthropic/claude-sonnet-4-5",
        "default_user": "User1",
        "default_account": "Main Card",
        "backup_keep": 30,
        "confidence_threshold": 0.8,
        "yadisk_path": "/budget/Budget.xlsx",
    }
    if allowed_users is not None:
        data["allowed_users"] = allowed_users
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(data))
    env = {
        "TELEGRAM_TOKEN": "123:ABC",
        "OPENROUTER_API_KEY": "or-key",
        "YADISK_TOKEN": "yd-token",
    }

    with patch.dict(os.environ, env, clear=True):
        from config import load_config
        with pytest.raises(ValueError, match="allowed_users"):
            load_config(str(cfg_file))
