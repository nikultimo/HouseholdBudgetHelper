"""Langfuse dataset management for the budget agent eval.

Creates/updates a dataset named 'budget-agent-eval' and uploads scores
from eval runs to Langfuse traces.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from eval_cases import EVAL_CASES, EvalCase

logger = logging.getLogger(__name__)

DATASET_NAME = "budget-agent-eval"


def _get_langfuse():
    """Return a Langfuse client or raise if not configured."""
    from langfuse import Langfuse
    return Langfuse(
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
        secret_key=os.environ["LANGFUSE_SECRET_KEY"],
        host=os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
    )


def upload_dataset_items(cases: list[EvalCase] | None = None) -> None:
    """Create or update the eval dataset in Langfuse with test case metadata.

    This is idempotent — running it multiple times won't duplicate items if
    the same item id is used.
    """
    lf = _get_langfuse()
    cases = cases or EVAL_CASES

    try:
        lf.create_dataset(name=DATASET_NAME, description="Budget agent regression eval cases")
    except Exception:
        pass  # dataset may already exist

    for case in cases:
        try:
            lf.create_dataset_item(
                dataset_name=DATASET_NAME,
                id=case.id,
                input={"question": case.question, "today": case.today},
                expected_output={"reference": case.reference, "notes": case.notes},
            )
            logger.info("Uploaded dataset item %s", case.id)
        except Exception as exc:
            logger.warning("Failed to upload item %s: %s", case.id, exc)

    lf.flush()
    print(f"Uploaded {len(cases)} items to Langfuse dataset '{DATASET_NAME}'")


def upload_scores(
    case_id: str,
    scores: dict[str, float],
    trace_id: str | None = None,
) -> None:
    """Upload a dict of metric scores to Langfuse for a given eval case.

    If trace_id is None, creates a standalone score not linked to a trace.
    """
    lf = _get_langfuse()
    # langfuse v4 requires an OTEL-format trace id (32-char hex); generate a
    # deterministic-seeded one per case when the run did not supply a real trace.
    run_id = trace_id or lf.create_trace_id(seed=f"eval-{case_id}")

    for metric_name, value in scores.items():
        if metric_name.startswith("_"):
            continue  # skip internal keys like _error
        if value is None or float(value) < 0:
            continue  # skip n/a metrics (e.g. factual_correctness with no prose ref)
        try:
            lf.create_score(
                name=metric_name,
                value=float(value),
                trace_id=run_id,
                comment=f"case={case_id}",
            )
        except Exception as exc:
            logger.warning("Failed to upload score %s for case %s: %s", metric_name, case_id, exc)

    lf.flush()
