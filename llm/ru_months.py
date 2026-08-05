"""Single source of truth for Russian calendar-month name matching.

Multiple modules (dispatcher, query planner, analysis, and the budget agent)
each need to recognize a Russian month name inside free-form text. Keeping
one canonical stem list here means a spelling fix or added form only needs
to happen once.
"""
from __future__ import annotations

import re

# (regex stem, month number), in calendar order.
MONTH_STEMS: tuple[tuple[str, int], ...] = (
    (r"январ\w*", 1),
    (r"феврал\w*", 2),
    (r"март\w*", 3),
    (r"апрел\w*", 4),
    (r"ма[йяе]", 5),
    (r"июн\w*", 6),
    (r"июл\w*", 7),
    (r"август\w*", 8),
    (r"сентябр\w*", 9),
    (r"октябр\w*", 10),
    (r"ноябр\w*", 11),
    (r"декабр\w*", 12),
)

MONTH_ALTERNATION = "|".join(stem for stem, _ in MONTH_STEMS)
MONTH_WORD_RE = re.compile(rf"\b(?:{MONTH_ALTERNATION})\b", re.IGNORECASE)


def find_month(text: str) -> tuple[int, int] | None:
    """Return (month number, match start index) for the first month word in text."""
    match = MONTH_WORD_RE.search(text)
    if not match:
        return None
    word = match.group(0)
    for stem, number in MONTH_STEMS:
        if re.fullmatch(stem, word, re.IGNORECASE):
            return number, match.start()
    return None
