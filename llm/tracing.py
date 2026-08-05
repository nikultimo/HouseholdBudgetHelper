from __future__ import annotations
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_langfuse_instance = None
_langfuse_not_configured = False
_langfuse_retry_after_ts = 0.0
_langfuse_client = None  # module-level alias for test patching

_LANGFUSE_RETRY_COOLDOWN_SECONDS = 60.0


def _get_langfuse_client():
    global _langfuse_instance, _langfuse_not_configured, _langfuse_retry_after_ts
    if _langfuse_not_configured:
        return None
    now = time.monotonic()
    if now < _langfuse_retry_after_ts:
        return None
    if _langfuse_instance is None:
        pk = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
        sk = os.environ.get("LANGFUSE_SECRET_KEY", "")
        _looks_real = pk.startswith("pk-lf-") and len(pk) > 20 and pk != "pk-lf-budget-bot-key"
        if not pk or not sk or pk.startswith("your_") or not _looks_real:
            logger.info("Langfuse not configured — tracing disabled")
            _langfuse_not_configured = True
            return None
        try:
            from langfuse import get_client
            _langfuse_instance = get_client()
        except Exception as exc:
            _langfuse_retry_after_ts = now + _LANGFUSE_RETRY_COOLDOWN_SECONDS
            logger.warning(
                "Langfuse init failed, tracing will retry in %.0fs: %s",
                _LANGFUSE_RETRY_COOLDOWN_SECONDS,
                exc,
            )
            return None
    return _langfuse_instance


def reset_langfuse_client() -> None:
    global _langfuse_instance, _langfuse_not_configured, _langfuse_retry_after_ts
    _langfuse_instance = None
    _langfuse_not_configured = False
    _langfuse_retry_after_ts = 0.0


@dataclass
class TraceContext:
    _lf: Any
    _root_cm: Any
    _root_span: Any
    _output: dict[str, Any] = field(default_factory=dict)

    def child_generation(
        self,
        name: str,
        model: str,
        input: dict,
        output: Any,
        usage: dict | None = None,
        reasoning: str = "",
    ) -> None:
        if self._lf is None:
            return
        try:
            gen_cm = self._lf.start_as_current_observation(
                name=name, as_type="generation", model=model, input=input
            )
            gen_span = gen_cm.__enter__()
            metadata = {"reasoning": reasoning} if reasoning else None
            gen_span.update(
                output=output,
                usage_details=usage or {},
                metadata=metadata,
            )
            gen_cm.__exit__(None, None, None)
        except Exception as exc:
            logger.warning("Langfuse child_generation failed: %s", exc)

    def set_metadata(self, key: str, value: Any) -> None:
        if self._lf is None or self._root_span is None:
            return
        try:
            self._root_span.update(metadata={key: value})
        except Exception as exc:
            logger.warning("Langfuse set_metadata failed: %s", exc)

    def set_reply(self, reply: str) -> None:
        """Persist the exact Telegram reply without waiting for ``finish``."""
        self._output["reply"] = reply
        if self._lf is None or self._root_span is None:
            return
        try:
            self._root_span.update(output=dict(self._output))
        except Exception as exc:
            logger.warning("Langfuse set_reply failed: %s", exc)

    def finish(self, output: Any = None) -> None:
        if self._lf is None or self._root_cm is None:
            return
        try:
            if not self._output and output is not None and self._root_span is not None:
                self._root_span.update(output=output)
            self._root_cm.__exit__(None, None, None)
            self._lf.flush()
        except Exception as exc:
            logger.warning("Langfuse finish failed: %s", exc)


def start_trace(user_id: str, input_text: str) -> TraceContext:
    lf = globals().get("_langfuse_client") or _get_langfuse_client()
    if lf is None:
        return TraceContext(_lf=None, _root_cm=None, _root_span=None)
    try:
        root_cm = lf.start_as_current_observation(
            name="request",
            as_type="span",
            input={"text": input_text},
            metadata={"user_id": user_id},
        )
        root_span = root_cm.__enter__()
        return TraceContext(_lf=lf, _root_cm=root_cm, _root_span=root_span)
    except Exception as exc:
        logger.warning("Langfuse start_trace failed, tracing disabled: %s", exc)
        return TraceContext(_lf=None, _root_cm=None, _root_span=None)


def read_trace_for_diagnostics(
    trace_id: str,
    *,
    max_attempts: int = 3,
    read_timeout_seconds: float = 5.0,
    retry_delay_seconds: float = 0.5,
) -> dict[str, Any] | None:
    """Read one Langfuse trace for smoke diagnostics with bounded retries.

    This helper is deliberately separate from request tracing. A slow or
    unavailable diagnostics endpoint therefore cannot affect bot replies.
    """
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com").rstrip("/")
    if not trace_id or not public_key or not secret_key:
        return None
    timeout = httpx.Timeout(read_timeout_seconds, connect=min(read_timeout_seconds, 5.0))
    url = f"{host}/api/public/traces/{trace_id}"
    for attempt in range(1, max(1, max_attempts) + 1):
        try:
            response = httpx.get(
                url,
                auth=(public_key, secret_key),
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else None
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500 and exc.response.status_code != 429:
                logger.warning("Langfuse diagnostic trace read rejected: %s", exc)
                return None
            error: Exception = exc
        except httpx.TransportError as exc:
            error = exc
        except Exception as exc:
            logger.warning("Langfuse diagnostic trace read failed: %s", exc)
            return None
        if attempt < max(1, max_attempts):
            logger.warning(
                "Langfuse diagnostic trace read failed (%s); attempt %d/%d",
                type(error).__name__,
                attempt,
                max(1, max_attempts),
            )
            time.sleep(max(0.0, retry_delay_seconds) * attempt)
    logger.warning("Langfuse diagnostic trace read failed after %d attempts", max(1, max_attempts))
    return None
