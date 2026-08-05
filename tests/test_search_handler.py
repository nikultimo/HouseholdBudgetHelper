from datetime import date

from handlers.search import SearchQuery, _filter_transactions, _format_search_results


def test_filter_transactions_returns_newest_first():
    txns = [
        {"date": date(2026, 4, 21), "row": 10, "description": "old", "category": "Всякое", "type": "Расход", "amount": 100, "month": "2026-04"},
        {"date": date(2026, 4, 27), "row": 20, "description": "new same day first", "category": "Всякое", "type": "Расход", "amount": 200, "month": "2026-04"},
        {"date": date(2026, 4, 27), "row": 21, "description": "new same day second", "category": "Всякое", "type": "Расход", "amount": 300, "month": "2026-04"},
        {"date": date(2026, 4, 26), "row": 30, "description": "middle", "category": "Всякое", "type": "Расход", "amount": 400, "month": "2026-04"},
    ]

    results = _filter_transactions(txns, SearchQuery(limit=3))

    assert [t["description"] for t in results] == [
        "new same day second",
        "new same day first",
        "middle",
    ]


def test_format_search_results_preserves_result_order():
    results = [
        {"date": date(2026, 4, 27), "description": "new", "category": "Всякое", "type": "Расход", "amount": 200},
        {"date": date(2026, 4, 21), "description": "old", "category": "Всякое", "type": "Расход", "amount": 100},
    ]

    text = _format_search_results(results, SearchQuery())

    assert text.index("new") < text.index("old")
