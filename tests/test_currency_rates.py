import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import currency.rates as rates_module


CBR_SAMPLE_XML = """<?xml version="1.0" encoding="windows-1251"?>
<ValCurs Date="28.04.2026" name="Foreign Currency Market">
  <Valute ID="R01235">
    <NumCode>840</NumCode>
    <CharCode>USD</CharCode>
    <Nominal>1</Nominal>
    <Name>Доллар США</Name>
    <Value>90,0000</Value>
    <VunitRate>90,0000</VunitRate>
  </Valute>
  <Valute ID="R01239">
    <NumCode>978</NumCode>
    <CharCode>EUR</CharCode>
    <Nominal>1</Nominal>
    <Name>Евро</Name>
    <Value>100,5000</Value>
    <VunitRate>100,5000</VunitRate>
  </Valute>
  <Valute ID="R01760">
    <NumCode>392</NumCode>
    <CharCode>JPY</CharCode>
    <Nominal>100</Nominal>
    <Name>Японских иен</Name>
    <Value>60,0000</Value>
    <VunitRate>0,6000</VunitRate>
  </Valute>
</ValCurs>"""


def test_parse_cbr_xml_basic():
    rates = rates_module._parse_cbr_xml(CBR_SAMPLE_XML)
    assert rates["RUB"] == 1.0
    assert rates["USD"] == pytest.approx(90.0)
    assert rates["EUR"] == pytest.approx(100.5)


def test_parse_cbr_xml_nominal_gt_one():
    rates = rates_module._parse_cbr_xml(CBR_SAMPLE_XML)
    # 100 JPY = 60 RUB => 1 JPY = 0.6 RUB
    assert rates["JPY"] == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_convert_rub_is_noop():
    rub, rate = await rates_module.convert_to_rub(500.0, "RUB")
    assert rub == 500.0
    assert rate == 1.0


@pytest.mark.asyncio
async def test_convert_usd_uses_rate():
    rates_module._cache = None
    mock_resp = MagicMock()
    mock_resp.text = CBR_SAMPLE_XML
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        rub, rate = await rates_module.convert_to_rub(50.0, "USD")

    assert rub == pytest.approx(4500.0)
    assert rate == pytest.approx(90.0)


@pytest.mark.asyncio
async def test_convert_unknown_currency_raises():
    rates_module._cache = None
    mock_resp = MagicMock()
    mock_resp.text = CBR_SAMPLE_XML
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        with pytest.raises(ValueError, match="Unknown currency"):
            await rates_module.convert_to_rub(100.0, "XYZ")


@pytest.mark.asyncio
async def test_get_rates_uses_stale_cache_on_network_failure():
    from datetime import date, timedelta
    stale_rates = {"RUB": 1.0, "USD": 88.0}
    rates_module._cache = (date.today() - timedelta(days=1), stale_rates)

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("network error"))
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await rates_module.get_rates()

    assert result["USD"] == 88.0
    # restore
    rates_module._cache = None
