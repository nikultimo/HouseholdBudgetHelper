from __future__ import annotations
import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from salary.calculator import PaymentInfo


def _make_info(net: float, period_days: int, month_days: int) -> PaymentInfo:
    return PaymentInfo(
        payment_date=date(2026, 5, 5),
        period_start=date(2026, 4, 16),
        period_end=date(2026, 4, 30),
        working_days_period=period_days,
        working_days_month=month_days,
        monthly_salary=100000.0,
        gross_amount=round(net / 0.87, 2),
        net_amount=net,
        mandatory_payments=[],
        mandatory_total=0.0,
        remainder=net,
    )


@pytest.fixture
def mock_update():
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    update.message.chat.send_action = AsyncMock()
    return update


@pytest.fixture
def mock_context():
    return MagicMock()


@pytest.fixture
def mock_reader():
    reader = MagicMock()
    reader.get_settings.return_value = {"Зарплата User1": 100000.0}
    return reader


@pytest.fixture
def mock_writer():
    writer = MagicMock()
    writer.update_capital_salary.return_value = (
        ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"],
        {"2026-04": 0.0, "2026-05": 95238.0},
    )
    return writer


@pytest.fixture
def mock_yadisk():
    yadisk = MagicMock()
    yadisk.upload = AsyncMock()
    return yadisk


@pytest.mark.asyncio
async def test_cmd_update_capital_success(mock_update, mock_context, mock_reader, mock_writer, mock_yadisk):
    info = _make_info(net=47619.0, period_days=10, month_days=21)

    with patch("handlers.capital.calculate_payment", return_value=info), \
         patch("handlers.capital.date") as mock_date:
        mock_date.today.return_value = date(2026, 4, 17)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        from handlers.capital import cmd_update_capital
        await cmd_update_capital(mock_update, mock_context, mock_reader, mock_writer, mock_yadisk)

    mock_reader.invalidate_cache.assert_called_once()
    mock_writer.update_capital_salary.assert_called_once()
    months_arg = mock_writer.update_capital_salary.call_args[0][0]
    assert months_arg[0] == "2026-04"
    assert len(months_arg) == 7
    assert months_arg[-1] == "2026-10"
    mock_yadisk.upload.assert_called_once()
    reply_text = mock_update.message.reply_text.call_args[0][0]
    assert "✅" in reply_text
    assert "2026-04" in reply_text


@pytest.mark.asyncio
async def test_cmd_update_capital_no_salary(mock_update, mock_context, mock_yadisk):
    reader = MagicMock()
    reader.get_settings.return_value = {}
    writer = MagicMock()

    from handlers.capital import cmd_update_capital
    await cmd_update_capital(mock_update, mock_context, reader, writer, mock_yadisk)

    writer.update_capital_salary.assert_not_called()
    mock_yadisk.upload.assert_not_called()
    reply_text = mock_update.message.reply_text.call_args[0][0]
    assert "Зарплата" in reply_text


@pytest.mark.asyncio
async def test_cmd_update_capital_missing_months(mock_update, mock_context, mock_reader, mock_yadisk):
    writer = MagicMock()
    # Only 5 of 7 months were found
    writer.update_capital_salary.return_value = (
        ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08"],
        {},
    )
    info = _make_info(net=47619.0, period_days=10, month_days=21)

    with patch("handlers.capital.calculate_payment", return_value=info), \
         patch("handlers.capital.date") as mock_date:
        mock_date.today.return_value = date(2026, 4, 17)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        from handlers.capital import cmd_update_capital
        await cmd_update_capital(mock_update, mock_context, mock_reader, writer, mock_yadisk)

    reply_text = mock_update.message.reply_text.call_args[0][0]
    assert "⚠️" in reply_text
    assert "2026-09" in reply_text or "2026-10" in reply_text


@pytest.mark.asyncio
async def test_cmd_update_capital_yadisk_failure(mock_update, mock_context, mock_reader, mock_writer):
    yadisk = MagicMock()
    yadisk.upload = AsyncMock(side_effect=Exception("network error"))
    info = _make_info(net=47619.0, period_days=10, month_days=21)

    with patch("handlers.capital.calculate_payment", return_value=info), \
         patch("handlers.capital.date") as mock_date:
        mock_date.today.return_value = date(2026, 4, 17)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        from handlers.capital import cmd_update_capital
        await cmd_update_capital(mock_update, mock_context, mock_reader, mock_writer, yadisk)

    reply_text = mock_update.message.reply_text.call_args[0][0]
    assert "⚠️" in reply_text
    assert "Яндекс Диск" in reply_text


def test_build_months_current_plus_six():
    from handlers.capital import _build_months
    months = _build_months(date(2026, 4, 17))
    assert months == ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]


def test_build_months_wraps_year():
    from handlers.capital import _build_months
    months = _build_months(date(2026, 10, 1))
    assert months == ["2026-10", "2026-11", "2026-12", "2027-01", "2027-02", "2027-03", "2027-04"]
