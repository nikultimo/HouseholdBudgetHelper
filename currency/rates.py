from __future__ import annotations
import logging
import xml.etree.ElementTree as ET
from datetime import date
import httpx

logger = logging.getLogger(__name__)

_cache: tuple[date, dict[str, float]] | None = None
CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp"


async def get_rates() -> dict[str, float]:
    """Return {ISO_code: RUB_per_unit}. Fetched from CBR, cached for the calendar day."""
    global _cache
    today = date.today()
    if _cache is not None and _cache[0] == today:
        return _cache[1]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(CBR_URL)
            resp.raise_for_status()
        rates = _parse_cbr_xml(resp.text)
        _cache = (today, rates)
        logger.info("CBR rates fetched: %d currencies", len(rates) - 1)
        return rates
    except Exception as exc:
        logger.warning("Failed to fetch CBR rates: %s", exc)
        if _cache is not None:
            logger.warning("Using stale CBR rates from %s", _cache[0])
            return _cache[1]
        return {}


def _parse_cbr_xml(xml_text: str) -> dict[str, float]:
    root = ET.fromstring(xml_text)
    rates: dict[str, float] = {"RUB": 1.0}
    for valute in root.findall("Valute"):
        code = (valute.findtext("CharCode") or "").strip()
        nominal_str = (valute.findtext("Nominal") or "1").strip()
        value_str = (valute.findtext("Value") or "0").strip().replace(",", ".")
        try:
            nominal = int(nominal_str)
            rates[code] = float(value_str) / nominal
        except (ValueError, ZeroDivisionError):
            continue
    return rates


async def convert_to_rub(amount: float, currency: str) -> tuple[float, float]:
    """Return (rub_amount, rate). rate=1.0 for RUB. Raises ValueError for unknown currency."""
    currency = currency.strip().upper()
    if currency in ("RUB", "РУБ", "Р", "₽", ""):
        return round(amount, 2), 1.0
    rates = await get_rates()
    rate = rates.get(currency)
    if rate is None:
        raise ValueError(f"Unknown currency: {currency}")
    return round(amount * rate, 2), rate
