#!/usr/bin/env python3
"""Read-only, redacted audit of recent TelegramBudgetHelper Langfuse traces."""
from __future__ import annotations

import argparse
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx


def _text(value: Any, key: str) -> str:
    if isinstance(value, dict):
        return str(value.get(key) or "")
    return str(value or "")


def _flags(trace: dict[str, Any]) -> list[str]:
    metadata = trace.get("metadata") or {}
    query_plan = metadata.get("query_plan") or {}
    question = _text(trace.get("input"), "text").casefold()
    reply = _text(trace.get("output"), "reply")
    route = str(metadata.get("route") or "")
    operation = str(query_plan.get("operation") or "")
    flags: list[str] = []
    if not route:
        flags.append("missing_route")
    if not reply:
        flags.append("missing_reply")
    if re.search(r"\b(?:трат\w*|потрат\w*|расход\w*)\b", question) and operation == "balance_at_date":
        flags.append("expense_as_balance")
    if re.search(r"\b(?:отпуск\w*|отпускн\w*)\b", question) and route != "budget_agent":
        flags.append("leave_wrong_route")
    if re.search(r"(^|\n)\s*[*_-]\s+", reply):
        flags.append("markdown_in_html_reply")
    return flags


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    if not public_key or not secret_key:
        raise SystemExit("Langfuse is not configured.")
    host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com").rstrip("/")
    start = datetime.now(timezone.utc) - timedelta(days=max(1, args.days))

    traces: list[dict[str, Any]] = []
    page = 1
    with httpx.Client(auth=(public_key, secret_key), timeout=10.0) as client:
        while len(traces) < max(1, args.limit):
            response = client.get(
                f"{host}/api/public/traces",
                params={
                    "page": page,
                    "limit": min(100, args.limit - len(traces)),
                    "fromTimestamp": start.isoformat(),
                },
            )
            response.raise_for_status()
            payload = response.json()
            batch = payload.get("data", [])
            traces.extend(batch)
            if not batch or page >= int((payload.get("meta") or {}).get("totalPages") or page):
                break
            page += 1

    flagged = [(trace, _flags(trace)) for trace in traces]
    flagged = [(trace, flags) for trace, flags in flagged if flags]
    counts = Counter(flag for _, flags in flagged for flag in flags)
    print(f"Audited traces: {len(traces)}; flagged: {len(flagged)}")
    print("Flag counts:", dict(sorted(counts.items())))
    for trace, flags in flagged:
        # Intentionally exclude question, reply, user metadata, and financial values.
        print(
            f"{trace.get('timestamp', '<unknown>')} "
            f"trace={trace.get('id', '<unknown>')} flags={','.join(flags)}"
        )


if __name__ == "__main__":
    main()
