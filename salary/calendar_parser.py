"""Parses Russian production calendar from consultant.ru and caches it locally."""
from __future__ import annotations
import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_CACHE_FILE = Path(__file__).parent.parent / "data" / "prod_calendar_{year}.json"
_URL = "https://www.consultant.ru/law/ref/calendar/proizvodstvennye/{year}/"
_MONTHS_RU = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


def _parse_html(html: str, year: int) -> dict[str, str]:
    """Returns {YYYY-MM-DD: 'work'|'holiday'|'weekend'} for all days."""
    tables = re.findall(
        r'<table[^>]*class="cal"[^>]*>(.*?)</table>', html, re.DOTALL
    )
    result: dict[str, str] = {}
    for month_idx, table in enumerate(tables):
        month = month_idx + 1
        cells = re.findall(r'<td class="([^"]*)"[^>]*>(\d+)', table)
        for cls, day_str in cells:
            day = int(day_str)
            d = date(year, month, day)
            classes = cls.split()
            if "holiday" in classes:
                result[d.isoformat()] = "holiday"
            elif "weekend" in classes:
                result[d.isoformat()] = "weekend"
            else:
                result[d.isoformat()] = "work"
    return result


def fetch_calendar(year: int, force: bool = False) -> dict[str, str]:
    """Return production calendar for year. Uses cache if available."""
    cache_path = Path(str(_CACHE_FILE).format(year=year))
    if not force and cache_path.exists():
        return json.loads(cache_path.read_text())

    logger.info("Fetching production calendar for %d from consultant.ru", year)
    try:
        r = httpx.get(
            _URL.format(year=year),
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
            follow_redirects=True,
        )
        r.raise_for_status()
        calendar = _parse_html(r.text, year)
        if len(calendar) < 200:
            raise ValueError(f"Parsed only {len(calendar)} days, expected ~250+")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(calendar, ensure_ascii=False, indent=2))
        logger.info("Cached production calendar: %d days", len(calendar))
        return calendar
    except Exception as exc:
        logger.error("Failed to fetch production calendar: %s", exc)
        if cache_path.exists():
            logger.warning("Using stale cache for year %d", year)
            return json.loads(cache_path.read_text())
        logger.warning(
            "No production calendar cache for year %d; falling back to all-days-work mode", year
        )
        return {}


def public_holidays_in_period(
    start: date,
    end: date,
    calendar: dict[str, str],
) -> list[date]:
    """Return public holidays (праздники ТК РФ ст. 112) in [start, end] inclusive.

    New-format calendars (fetched after the holiday/weekend split fix) mark them as
    "holiday". Old cached files use "weekend" for both — the fallback identifies
    them by weekday: a Mon–Fri marked "weekend" must be a public holiday, not a
    natural Saturday/Sunday.
    """
    days = []
    cur = start
    while cur <= end:
        val = calendar.get(cur.isoformat(), "work")
        if val == "holiday" or (val == "weekend" and cur.weekday() < 5):
            days.append(cur)
        cur += timedelta(days=1)
    return days


def working_days_in_period(
    start: date,
    end: date,
    calendar: dict[str, str],
) -> list[date]:
    """Return list of working days in [start, end] inclusive."""
    days = []
    cur = start
    while cur <= end:
        if calendar.get(cur.isoformat(), "work") == "work":
            days.append(cur)
        cur += timedelta(days=1)
    return days
