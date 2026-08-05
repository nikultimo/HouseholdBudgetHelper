"""Scoring functions for budget agent evaluation.

Two layers:
  1. Deterministic scorers — instant, no LLM calls.
  2. RAGAS LLM scorers    — use OpenRouter via ragas.llms.llm_factory.

All scores are floats in [0.0, 1.0].
"""
from __future__ import annotations

import os
import re
from typing import Any

from llm.agent import _validate_agent_answer


# ---------------------------------------------------------------------------
# Deterministic scorers
# ---------------------------------------------------------------------------

def score_contains(answer: str, expected: list[str]) -> float:
    """1.0 if ALL expected substrings appear in the answer (case-insensitive)."""
    if not expected:
        return 1.0
    answer_lower = answer.lower()
    hits = sum(1 for s in expected if s.lower() in answer_lower)
    return hits / len(expected)


def score_not_contains(answer: str, forbidden: list[str]) -> float:
    """1.0 if NONE of the forbidden substrings appear (case-insensitive)."""
    if not forbidden:
        return 1.0
    answer_lower = answer.lower()
    violations = sum(1 for s in forbidden if s.lower() in answer_lower)
    return 0.0 if violations else 1.0


def score_numeric(answer: str, reference: str, tolerance: float = 0.05) -> float:
    """1.0 if any number in the answer is within ±tolerance of the reference number.

    Returns 1.0 (N/A) when reference is empty.
    Handles Russian/comma-thousands formatting: '2 000', '2,000', '2,000,000'.
    """
    if not reference.strip():
        return 1.0  # no numeric assertion for this case

    try:
        expected = float(reference.replace(" ", "").replace(",", ""))
    except ValueError:
        return 1.0  # reference is not a pure number — skip numeric check

    # Normalise answer: collapse space-thousands ("2 000" → "2000")
    normalized = re.sub(r"(\d)[  ](\d)", r"\1\2", answer)
    # Match integer or decimal numbers, including comma-grouped (2,000 / 2,000,000)
    numbers = re.findall(r"\d[\d,]*(?:\.\d+)?", normalized)
    for raw in numbers:
        try:
            # Remove commas (thousands separator) then parse
            val = float(raw.replace(",", ""))
            if expected == 0:
                if val == 0:
                    return 1.0
                continue
            if abs(val - expected) / abs(expected) <= tolerance:
                return 1.0
        except ValueError:
            continue
    return 0.0


def score_tool_usage(messages: list[dict[str, Any]], expected_tools: list[str]) -> float:
    """Fraction of expected tools that were actually called in the conversation."""
    if not expected_tools:
        return 1.0
    called: set[str] = set()
    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                called.add(fn.get("name", ""))
    hits = sum(1 for t in expected_tools if t in called)
    return hits / len(expected_tools)


def score_numeric_grounding(answer: str, tool_results: list[str]) -> float:
    """1.0 when every numeric claim is present in tool evidence."""
    flags = _validate_agent_answer(answer, tool_results, "")
    return 0.0 if "unsupported_numeric_claim" in flags else 1.0


def score_telegram_html(answer: str) -> float:
    """1.0 for balanced supported Telegram HTML without Markdown bullets."""
    flags = _validate_agent_answer(answer, [], "")
    return 0.0 if "invalid_telegram_html" in flags else 1.0


def extract_tool_results(messages: list[dict[str, Any]]) -> list[str]:
    """Extract all tool result strings from the conversation history."""
    results = []
    for msg in messages:
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if content:
                results.append(str(content))
    return results


def extract_final_answer(messages: list[dict[str, Any]]) -> str:
    """Extract the last assistant message content (the final answer)."""
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            return str(msg["content"])
    return ""


# ---------------------------------------------------------------------------
# RAGAS LLM scorers
#
# We use OpenRouter as the judge LLM. OpenRouter does NOT serve an embeddings
# endpoint, so embedding-based metrics (AnswerRelevancy, AnswerCorrectness,
# SemanticSimilarity) cannot run here. We use the two LLM-only metrics that
# work over OpenRouter and complement the deterministic scorers:
#   • Faithfulness     — is the answer grounded in the tool results? (no hallucination)
#   • FactualCorrectness — does the answer match the prose ground truth? (LLM judge)
# Faithfulness needs only response + retrieved_contexts. FactualCorrectness
# needs a prose reference (EvalCase.reference_answer); it is skipped otherwise.
# ---------------------------------------------------------------------------

import math


def _norm_score(value: Any) -> float:
    """Coerce a RAGAS score to a float in [0, 1]; NaN/None/errors → -1.0 (n/a)."""
    if value is None:
        return -1.0
    try:
        f = float(value)
    except (TypeError, ValueError):
        return -1.0
    if math.isnan(f):
        return -1.0
    return f


def build_ragas_llm():
    """Return a RAGAS-compatible LLM configured for OpenRouter."""
    from openai import OpenAI
    from ragas.llms import llm_factory

    client = OpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
    )
    model = os.environ.get("EVAL_MODEL", "google/gemini-2.5-flash-lite")
    return llm_factory(model, provider="openai", client=client)


async def run_ragas_metrics(
    user_input: str,
    response: str,
    retrieved_contexts: list[str],
    reference: str,
    ragas_llm: Any,
    reference_answer: str = "",
) -> dict[str, float]:
    """Run RAGAS Faithfulness (+ FactualCorrectness when a prose reference exists).

    `reference` is the numeric ground truth (used elsewhere by score_numeric).
    `reference_answer` is a prose ground truth used by FactualCorrectness; when
    empty, only Faithfulness runs and factual_correctness is reported as -1.0 (n/a).

    Returns {"faithfulness": float, "factual_correctness": float}; -1.0 = n/a.
    `evaluate()` is synchronous, so we run it in a worker thread (it spins up its
    own event loop internally — calling it on the running loop would deadlock).
    """
    import asyncio

    from ragas import evaluate
    from ragas.dataset_schema import EvaluationDataset, SingleTurnSample
    from ragas.metrics import FactualCorrectness, Faithfulness

    prose_ref = reference_answer.strip()
    use_factual = bool(prose_ref)

    metrics = [Faithfulness(llm=ragas_llm)]
    if use_factual:
        metrics.append(FactualCorrectness(llm=ragas_llm))

    sample = SingleTurnSample(
        user_input=user_input,
        response=response or "(empty answer)",
        retrieved_contexts=retrieved_contexts if retrieved_contexts else ["(no tool results)"],
        reference=prose_ref or reference or "(no reference)",
    )
    dataset = EvaluationDataset(samples=[sample])

    try:
        result = await asyncio.to_thread(
            evaluate,
            dataset=dataset,
            metrics=metrics,
            llm=ragas_llm,
            raise_exceptions=False,
        )
        scores = result.scores[0] if result.scores else {}
        # FactualCorrectness key carries its mode, e.g. "factual_correctness(mode=f1)".
        factual_raw = next(
            (v for k, v in scores.items() if k.startswith("factual_correctness")),
            None,
        )
        return {
            "faithfulness": _norm_score(scores.get("faithfulness")),
            "factual_correctness": _norm_score(factual_raw) if use_factual else -1.0,
        }
    except Exception as exc:
        return {
            "faithfulness": -1.0,
            "factual_correctness": -1.0,
            "_error": str(exc),
        }
