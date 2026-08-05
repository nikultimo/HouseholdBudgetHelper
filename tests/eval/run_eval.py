"""Budget agent eval runner.

Usage:
    # From repo root:
    python tests/eval/run_eval.py --generate
    python tests/eval/run_eval.py --generate --upload
    python tests/eval/run_eval.py --cases e001,e003,e007
    python tests/eval/run_eval.py --generate --no-ragas   # skip LLM scoring (fast)

Environment:
    Requires OPENROUTER_API_KEY (and optionally LANGFUSE_* vars for --upload).
    No TELEGRAM_TOKEN or YADISK_TOKEN needed.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import sys
import time
from typing import TYPE_CHECKING

# Make repo root importable when running from tests/eval/
_REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from generate_eval_workbook import EVAL_WORKBOOK_PATH, generate as generate_workbook  # noqa: E402
from eval_cases import EVAL_CASES, EVAL_CASES_BY_ID, EvalCase  # noqa: E402
from scorers import (  # noqa: E402
    build_ragas_llm,
    extract_final_answer,
    extract_tool_results,
    run_ragas_metrics,
    score_contains,
    score_not_contains,
    score_numeric_grounding,
    score_numeric,
    score_telegram_html,
    score_tool_usage,
)

if TYPE_CHECKING:
    from excel.reader import ExcelReader
    from llm.client import LLMClient


def _build_llm_client() -> "LLMClient":  # type: ignore[name-defined]
    from llm.client import LLMClient
    return LLMClient(
        api_key=os.environ["OPENROUTER_API_KEY"],
        model=os.environ.get("EVAL_MODEL", "google/gemini-2.5-flash-lite"),
    )


def _fmt(score: float) -> str:
    if score < 0:
        return "  n/a"
    color = "\033[92m" if score >= 0.8 else ("\033[93m" if score >= 0.5 else "\033[91m")
    reset = "\033[0m"
    return f"{color}{score:.2f}{reset}"


def _fmt_bool(score: float) -> str:
    return "\033[92m✓\033[0m" if score >= 1.0 else "\033[91m✗\033[0m"


async def run_case(
    case: EvalCase,
    *,
    llm_client: "LLMClient",  # type: ignore[name-defined]
    reader: "ExcelReader",  # type: ignore[name-defined]
    ragas_llm: object | None,
    upload: bool,
) -> dict:
    from llm.agent import run_budget_agent_eval
    from llm.tips_loader import load_tips
    from llm.tracing import start_trace

    trace_ctx = start_trace(user_id="eval", input_text=case.question)

    t0 = time.monotonic()
    try:
        answer, messages = await run_budget_agent_eval(
            question=case.question,
            reader=reader,
            llm_client=llm_client,
            tips=load_tips(),
            today=case.today,
            default_user="User1",
            model=llm_client.model,
            trace_ctx=trace_ctx,
        )
    except Exception as exc:
        answer = f"[ERROR: {exc}]"
        messages = []

    elapsed = time.monotonic() - t0
    trace_ctx.finish(output=answer)
    trace_id = getattr(getattr(trace_ctx, "_root_span", None), "trace_id", None)

    # Extract contexts (tool results) and the final answer from message history
    contexts = extract_tool_results(messages)
    final = extract_final_answer(messages) or answer

    # --- Deterministic scores ---
    s_contains = score_contains(final, case.expected_contains)
    s_not_contains = score_not_contains(final, case.expected_not_contains)
    s_numeric = score_numeric(final, case.reference)
    s_tools = score_tool_usage(messages, case.expected_tools)
    s_grounded = score_numeric_grounding(final, contexts)
    s_html = score_telegram_html(final)

    # --- RAGAS scores (optional) ---
    ragas_scores: dict[str, float] = {}
    if ragas_llm is not None:
        ragas_scores = await run_ragas_metrics(
            user_input=case.question,
            response=final,
            retrieved_contexts=contexts,
            reference=case.reference,
            ragas_llm=ragas_llm,
            reference_answer=case.reference_answer,
        )

    all_scores = {
        "contains": s_contains,
        "not_contains": s_not_contains,
        "numeric": s_numeric,
        "tool_usage": s_tools,
        "numeric_grounding": s_grounded,
        "telegram_html": s_html,
        **ragas_scores,
    }

    if upload and os.environ.get("LANGFUSE_PUBLIC_KEY"):
        from langfuse_dataset import upload_scores
        upload_scores(case.id, all_scores, trace_id=trace_id)

    return {
        "case": case,
        "answer": final,
        "elapsed": elapsed,
        "scores": all_scores,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Budget agent eval runner")
    parser.add_argument("--generate", action="store_true", help="Regenerate eval_budget.xlsx")
    parser.add_argument("--upload", action="store_true", help="Upload scores to Langfuse")
    parser.add_argument("--upload-dataset", action="store_true", help="Upload dataset items to Langfuse then exit")
    parser.add_argument("--no-ragas", action="store_true", help="Skip RAGAS LLM scoring (faster)")
    parser.add_argument("--cases", type=str, default="", help="Comma-separated case IDs (default: all)")
    args = parser.parse_args()

    if args.generate:
        path = generate_workbook()
        print(f"Generated workbook: {path}")

    if not EVAL_WORKBOOK_PATH.exists():
        print(f"Workbook not found at {EVAL_WORKBOOK_PATH}. Run with --generate first.")
        sys.exit(1)

    if args.upload_dataset:
        from langfuse_dataset import upload_dataset_items
        upload_dataset_items()
        return

    from excel.reader import ExcelReader
    reader = ExcelReader(str(EVAL_WORKBOOK_PATH))
    llm_client = _build_llm_client()
    ragas_llm = None if args.no_ragas else build_ragas_llm()

    # Filter cases
    if args.cases:
        ids = [c.strip() for c in args.cases.split(",")]
        cases = [EVAL_CASES_BY_ID[i] for i in ids if i in EVAL_CASES_BY_ID]
        missing = [i for i in ids if i not in EVAL_CASES_BY_ID]
        if missing:
            print(f"Unknown case IDs: {missing}")
    else:
        cases = list(EVAL_CASES)

    print(f"\nRunning {len(cases)} eval cases against {EVAL_WORKBOOK_PATH.name}")
    print(f"Model: {llm_client.model}  RAGAS: {'disabled' if args.no_ragas else 'enabled'}\n")

    # Header
    col_w = 48
    ragas_cols = "" if args.no_ragas else "  faithful  factual"
    print(f"{'ID':<6} {'Question':<{col_w}} {'num':>4} {'tools':>5} {'cont':>4}{ragas_cols}  time")
    print("-" * (6 + col_w + 40 + (22 if not args.no_ragas else 0)))

    results = []
    for case in cases:
        result = await run_case(
            case,
            llm_client=llm_client,
            reader=reader,
            ragas_llm=ragas_llm,
            upload=args.upload,
        )
        results.append(result)
        s = result["scores"]
        q = case.question[:col_w]
        ragas_part = ""
        if not args.no_ragas:
            ragas_part = (
                f"  {_fmt(s.get('faithfulness', -1))}   "
                f"  {_fmt(s.get('factual_correctness', -1))}"
            )
        print(
            f"{case.id:<6} {q:<{col_w}} "
            f"{_fmt_bool(s['numeric']):>4} {_fmt_bool(s['tool_usage']):>5} "
            f"{_fmt_bool(s['contains']):>4}"
            f"{ragas_part}  {result['elapsed']:.1f}s"
        )

    # Summary
    n = len(results)
    avg = lambda key: sum(r["scores"].get(key, 0) for r in results) / n if n else 0

    def avg_ragas(key: str) -> float:
        """Average over cases where the metric actually ran (score >= 0)."""
        vals = [r["scores"].get(key, -1) for r in results]
        vals = [v for v in vals if v >= 0]
        return sum(vals) / len(vals) if vals else -1.0

    print(f"\nSummary ({n} cases):")
    print(f"  numeric accuracy:  {avg('numeric'):.0%}")
    print(f"  tool usage:        {avg('tool_usage'):.0%}")
    print(f"  numeric grounding: {avg('numeric_grounding'):.0%}")
    print(f"  Telegram HTML:     {avg('telegram_html'):.0%}")
    print(f"  contains check:    {avg('contains'):.0%}")
    if not args.no_ragas:
        print(f"  faithfulness:        {avg_ragas('faithfulness'):.2f}")
        print(f"  factual_correctness: {avg_ragas('factual_correctness'):.2f}")

    # Show failing cases
    fails = [
        r for r in results
        if r["scores"]["numeric"] < 1.0
        or r["scores"]["tool_usage"] < 1.0
        or r["scores"]["numeric_grounding"] < 1.0
        or r["scores"]["telegram_html"] < 1.0
    ]
    if fails:
        print(f"\nFailing cases ({len(fails)}):")
        for r in fails:
            print(f"  {r['case'].id}: {r['case'].question[:60]}")
            print(f"    answer: {r['answer'][:120]}")


if __name__ == "__main__":
    asyncio.run(main())
