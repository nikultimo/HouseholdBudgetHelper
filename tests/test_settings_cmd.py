import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch


def _make_update():
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


def _ctx():
    return MagicMock()


def test_get_salary_from_settings_falls_back_to_only_positive_salary():
    from handlers.salary import get_salary_from_settings

    salary = get_salary_from_settings(
        {
            "Зарплата User1": 388890,
            "Зарплата User2": 0,
        },
        default_user="User1",
    )

    assert salary == 388890


@pytest.mark.asyncio
async def test_salary_query_returns_all_salaries():
    from handlers.settings_cmd import cmd_salary_query
    reader = MagicMock()
    reader.get_settings.return_value = {
        "Зарплата User1": 100000,
        "Зарплата User2": 80000,
        "Доля User1": 0.5,
    }
    update = _make_update()
    await cmd_salary_query(update, _ctx(), reader=reader)
    text = update.message.reply_text.call_args[0][0]
    assert "Зарплата User1" in text
    assert "Зарплата User2" in text
    assert "Доля" not in text


@pytest.mark.asyncio
async def test_capital_query_when_sheet_missing():
    from handlers.settings_cmd import cmd_capital_query
    reader = MagicMock()
    reader.get_capital_data.return_value = {}
    update = _make_update()
    await cmd_capital_query(update, _ctx(), reader=reader)
    text = update.message.reply_text.call_args[0][0]
    assert "Капитал" in text


@pytest.mark.asyncio
async def test_capital_query_returns_current_actual_and_future_projection(monkeypatch):
    import handlers.settings_cmd as settings_cmd

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 4, 30)

    monkeypatch.setattr(settings_cmd, "datetime", FixedDatetime)
    reader = MagicMock()
    reader.get_capital_data.return_value = {
        "2026-03": {
            "rubles": 999999.0,
            "usd": 0.0,
            "investments": 0.0,
            "crypto": 0.0,
            "debts": 0.0,
            "salary_first": 0.0,
            "salary_second": 0.0,
        },
        "2026-04": {
            "rubles": 500000.0,
            "usd": 200000.0,
            "investments": 0.0,
            "crypto": 0.0,
            "debts": 100000.0,
            "salary_first": 50000.0,
            "salary_second": 50000.0,
        },
        "2026-05": {
            "rubles": 550000.0,
            "usd": 200000.0,
            "investments": 0.0,
            "crypto": 0.0,
            "debts": 100000.0,
            "salary_first": 70000.0,
            "salary_second": 80000.0,
        },
    }
    reader.get_mandatory_payments.return_value = {
        "first": [{"description": "Payment A", "amount": 10000.0}],
        "second": [{"description": "Payment B", "amount": 20000.0}],
    }
    update = _make_update()
    await settings_cmd.cmd_capital_query(update, _ctx(), reader=reader)
    text = update.message.reply_text.call_args[0][0]
    assert "2026-04" in text
    assert "600" in text  # 500k + 200k - 100k = 600k net
    assert "чистый" in text
    assert "2026-05" in text
    assert "~<b>720,000 ₽</b>" in text  # 600k + 150k salary - 30k payments
    assert "550,000 ₽</b>, долги" not in text
    assert "2026-03" not in text


@pytest.mark.asyncio
async def test_capital_update_calls_writer_and_upload():
    from handlers.settings_cmd import cmd_capital_update
    reader = MagicMock()
    writer = MagicMock()
    writer.reconcile_capital.return_value = 700000.0
    yadisk = MagicMock()
    yadisk.upload = AsyncMock()
    update = _make_update()
    await cmd_capital_update(update, _ctx(), extracted_value=700000.0, reader=reader, writer=writer, yadisk=yadisk)
    writer.reconcile_capital.assert_called_once()
    reader.invalidate_cache.assert_called_once()
    yadisk.upload.assert_awaited_once()
    assert "700" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_capital_update_reconciliation_is_uploaded():
    from handlers.settings_cmd import cmd_capital_update
    reader = MagicMock()
    writer = MagicMock()
    writer.reconcile_capital.return_value = 700000.0
    yadisk = MagicMock()
    yadisk.upload = AsyncMock()
    update = _make_update()
    await cmd_capital_update(update, _ctx(), extracted_value=700000.0, reader=reader, writer=writer, yadisk=yadisk)
    yadisk.upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_payments_checklist_formats_both_halves():
    from handlers.settings_cmd import cmd_payments_checklist
    reader = MagicMock()
    reader.get_mandatory_payments.return_value = {
        "first": [{"description": "Ипотека", "amount": 27000, "due_day": 5}],
        "second": [{"description": "Телефон", "amount": 500, "due_day": 20}],
    }
    update = _make_update()
    await cmd_payments_checklist(update, _ctx(), reader=reader)
    text = update.message.reply_text.call_args[0][0]
    assert "Ипотека" in text
    assert "Телефон" in text
    assert "☐" in text
    assert "27" in text


@pytest.mark.asyncio
async def test_salary_query_records_exact_trace_reply():
    from handlers.settings_cmd import cmd_salary_query

    reader = MagicMock()
    reader.get_settings.return_value = {"Зарплата User1": 100000}
    trace_ctx = MagicMock()

    with patch("handlers.settings_cmd.reply_to_update", new_callable=AsyncMock) as reply:
        await cmd_salary_query(
            MagicMock(), _ctx(), reader=reader, trace_ctx=trace_ctx,
        )

    reply.assert_awaited_once()
    assert reply.call_args.kwargs["trace_ctx"] is trace_ctx


@pytest.mark.asyncio
async def test_payments_checklist_records_exact_trace_reply():
    from handlers.settings_cmd import cmd_payments_checklist

    reader = MagicMock()
    reader.get_mandatory_payments.return_value = {"first": [], "second": []}
    trace_ctx = MagicMock()

    with patch("handlers.settings_cmd.reply_to_update", new_callable=AsyncMock) as reply:
        await cmd_payments_checklist(
            MagicMock(), _ctx(), reader=reader, trace_ctx=trace_ctx,
        )

    reply.assert_awaited_once()
    assert reply.call_args.kwargs["trace_ctx"] is trace_ctx


def _make_cfg():
    cfg = MagicMock()
    cfg.salary_pay_days = (5, 20)
    return cfg


@pytest.mark.asyncio
async def test_mandatory_payment_add_asks_for_description_when_missing():
    from handlers.settings_cmd import cmd_mandatory_payment_add

    reader, writer, yadisk = MagicMock(), MagicMock(), AsyncMock()
    with patch("handlers.settings_cmd.reply_to_update", new_callable=AsyncMock) as reply:
        await cmd_mandatory_payment_add(
            MagicMock(), _ctx(),
            description=None, amount=5000.0, due_day=None, half="first",
            reader=reader, writer=writer, yadisk=yadisk, cfg=_make_cfg(),
        )

    reply.assert_awaited_once()
    assert "название" in reply.call_args[0][2]
    writer.add_mandatory_payment.assert_not_called()


@pytest.mark.asyncio
async def test_mandatory_payment_add_asks_for_amount_when_missing():
    from handlers.settings_cmd import cmd_mandatory_payment_add

    reader, writer, yadisk = MagicMock(), MagicMock(), AsyncMock()
    with patch("handlers.settings_cmd.reply_to_update", new_callable=AsyncMock) as reply:
        await cmd_mandatory_payment_add(
            MagicMock(), _ctx(),
            description="Парковка", amount=None, due_day=None, half="first",
            reader=reader, writer=writer, yadisk=yadisk, cfg=_make_cfg(),
        )

    reply.assert_awaited_once()
    assert "сумму" in reply.call_args[0][2]
    writer.add_mandatory_payment.assert_not_called()


@pytest.mark.asyncio
async def test_mandatory_payment_add_asks_for_timing_when_no_half_or_day():
    from handlers.settings_cmd import cmd_mandatory_payment_add

    reader, writer, yadisk = MagicMock(), MagicMock(), AsyncMock()
    with patch("handlers.settings_cmd.reply_to_update", new_callable=AsyncMock) as reply:
        await cmd_mandatory_payment_add(
            MagicMock(), _ctx(),
            description="Парковка", amount=5000.0, due_day=None, half=None,
            reader=reader, writer=writer, yadisk=yadisk, cfg=_make_cfg(),
        )

    reply.assert_awaited_once()
    assert "Уточни" in reply.call_args[0][2]
    writer.add_mandatory_payment.assert_not_called()


@pytest.mark.asyncio
async def test_mandatory_payment_add_derives_half_from_due_day():
    from handlers.settings_cmd import cmd_mandatory_payment_add

    reader, writer, yadisk = MagicMock(), MagicMock(), AsyncMock()
    writer.add_mandatory_payment.return_value = 9

    with patch("handlers.settings_cmd.reply_to_update", new_callable=AsyncMock) as reply:
        await cmd_mandatory_payment_add(
            MagicMock(), _ctx(),
            description="Интернет", amount=900.0, due_day=22, half=None,
            reader=reader, writer=writer, yadisk=yadisk, cfg=_make_cfg(),
        )

    writer.add_mandatory_payment.assert_called_once_with(
        description="Интернет", amount=900.0, due_day=22, half="second",
    )
    reader.invalidate_cache.assert_called_once()
    yadisk.upload.assert_awaited_once()
    reply.assert_awaited_once()
    assert "Интернет" in reply.call_args[0][2]
    assert "900" in reply.call_args[0][2]


@pytest.mark.asyncio
async def test_mandatory_payment_add_uses_explicit_half_and_succeeds():
    from handlers.settings_cmd import cmd_mandatory_payment_add

    reader, writer, yadisk = MagicMock(), MagicMock(), AsyncMock()
    writer.add_mandatory_payment.return_value = 9

    with patch("handlers.settings_cmd.reply_to_update", new_callable=AsyncMock) as reply:
        await cmd_mandatory_payment_add(
            MagicMock(), _ctx(),
            description="Парковка", amount=5000.0, due_day=None, half="first",
            reader=reader, writer=writer, yadisk=yadisk, cfg=_make_cfg(),
        )

    writer.add_mandatory_payment.assert_called_once_with(
        description="Парковка", amount=5000.0, due_day=0, half="first",
    )
    reply.assert_awaited_once()
    assert "✅" in reply.call_args[0][2]


@pytest.mark.asyncio
async def test_mandatory_payment_add_reports_full_block_as_clarification():
    from handlers.settings_cmd import cmd_mandatory_payment_add

    reader, writer, yadisk = MagicMock(), MagicMock(), AsyncMock()
    writer.add_mandatory_payment.side_effect = ValueError("нет свободных строк в блоке первой половины")

    with patch("handlers.settings_cmd.reply_to_update", new_callable=AsyncMock) as reply:
        await cmd_mandatory_payment_add(
            MagicMock(), _ctx(),
            description="Парковка", amount=5000.0, due_day=None, half="first",
            reader=reader, writer=writer, yadisk=yadisk, cfg=_make_cfg(),
        )

    reply.assert_awaited_once()
    assert "нет свободных строк" in reply.call_args[0][2]
    reader.invalidate_cache.assert_not_called()
    yadisk.upload.assert_not_awaited()
