"""Telegram send/edit helpers shared between bot.py and dispatcher.py."""
from __future__ import annotations
import asyncio
import inspect
import logging
import os
import re
from typing import TYPE_CHECKING

from telegram import Update, CallbackQuery
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut
from telegram.ext import ContextTypes

if TYPE_CHECKING:
    from llm.tracing import TraceContext

logger = logging.getLogger(__name__)

_TELEGRAM_SEND_TIMEOUT_SEC = float(os.getenv("TELEGRAM_SEND_TIMEOUT_SEC", "12"))
_TELEGRAM_SEND_MAX_ATTEMPTS = int(os.getenv("TELEGRAM_SEND_MAX_ATTEMPTS", "3"))


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


async def _send_message_with_retries(
    *,
    bot,
    chat_id: int,
    text: str,
    parse_mode: str | None = None,
    reply_markup=None,
    reply_to_message_id: int | None = None,
    max_attempts: int = _TELEGRAM_SEND_MAX_ATTEMPTS,
) -> bool:
    for attempt in range(1, max_attempts + 1):
        try:
            result = bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                reply_to_message_id=reply_to_message_id,
            )
            if inspect.isawaitable(result):
                await asyncio.wait_for(result, timeout=_TELEGRAM_SEND_TIMEOUT_SEC)
            return True
        except RetryAfter as exc:
            delay = float(getattr(exc, "retry_after", 1.0))
            delay = min(max(delay, 0.5), 60.0)
            logger.warning(
                "Telegram RetryAfter %.1fs while sending message; attempt %d/%d",
                delay,
                attempt,
                max_attempts,
            )
            await asyncio.sleep(delay)
        except BadRequest as exc:
            logger.warning("Telegram send_message BadRequest: %s", exc)
            if parse_mode:
                plain = _strip_html(text)
                result = bot.send_message(
                    chat_id=chat_id,
                    text=plain,
                    reply_markup=reply_markup,
                    reply_to_message_id=reply_to_message_id,
                )
                if inspect.isawaitable(result):
                    await asyncio.wait_for(result, timeout=_TELEGRAM_SEND_TIMEOUT_SEC)
                return True
            return False
        except (TimedOut, NetworkError, asyncio.TimeoutError) as exc:
            backoff = min(2 ** (attempt - 1), 8)
            logger.warning(
                "Telegram send_message failed (%s: %s); attempt %d/%d; retry in %ss",
                type(exc).__name__,
                exc,
                attempt,
                max_attempts,
                backoff,
            )
            await asyncio.sleep(backoff)
        except Exception:
            logger.exception("Telegram send_message failed with unexpected error")
            return False

    logger.error("Telegram send_message failed after %d attempts", max_attempts)
    return False


async def _edit_callback_message_with_retries(
    query: CallbackQuery,
    text: str,
    *,
    parse_mode: str | None = None,
    reply_markup=None,
    max_attempts: int = _TELEGRAM_SEND_MAX_ATTEMPTS,
) -> bool:
    for attempt in range(1, max_attempts + 1):
        try:
            await asyncio.wait_for(
                query.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup),
                timeout=_TELEGRAM_SEND_TIMEOUT_SEC,
            )
            return True
        except RetryAfter as exc:
            delay = float(getattr(exc, "retry_after", 1.0))
            delay = min(max(delay, 0.5), 60.0)
            logger.warning(
                "Telegram RetryAfter %.1fs while editing message; attempt %d/%d",
                delay,
                attempt,
                max_attempts,
            )
            await asyncio.sleep(delay)
        except (TimedOut, NetworkError, asyncio.TimeoutError) as exc:
            backoff = min(2 ** (attempt - 1), 8)
            logger.warning(
                "Telegram edit_message_text failed (%s); attempt %d/%d; retry in %ss",
                type(exc).__name__,
                attempt,
                max_attempts,
                backoff,
            )
            await asyncio.sleep(backoff)
        except Exception:
            logger.exception("Telegram edit_message_text failed with unexpected error")
            return False

    logger.error("Telegram edit_message_text failed after %d attempts", max_attempts)
    return False


async def _send_or_notify(
    *,
    bot,
    chat_id: int,
    text: str,
    parse_mode: str | None,
    reply_markup,
    reply_to_message_id: int | None,
) -> None:
    ok = await _send_message_with_retries(
        bot=bot,
        chat_id=chat_id,
        text=text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        reply_to_message_id=reply_to_message_id,
    )
    if not ok:
        await _send_message_with_retries(
            bot=bot,
            chat_id=chat_id,
            text="⚠️ Не удалось доставить ответ (ошибка форматирования). Попробуй переформулировать вопрос.",
        )


async def reply_to_update(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    *,
    parse_mode: str | None = None,
    reply_markup=None,
    trace_ctx: "TraceContext | None" = None,
) -> None:
    if update.message:
        try:
            await asyncio.wait_for(
                update.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup),
                timeout=_TELEGRAM_SEND_TIMEOUT_SEC,
            )
            if trace_ctx is not None:
                trace_ctx.set_reply(text)
            return
        except RetryAfter as exc:
            delay = float(getattr(exc, "retry_after", 1.0))
            delay = min(max(delay, 0.5), 60.0)
            logger.warning("Telegram RetryAfter %.1fs while replying; falling back to background send", delay)
        except BadRequest as exc:
            logger.warning("Telegram reply_text BadRequest: %s; retrying as plain text", exc)
            if parse_mode:
                try:
                    plain = _strip_html(text)
                    await asyncio.wait_for(
                        update.message.reply_text(plain, reply_markup=reply_markup),
                        timeout=_TELEGRAM_SEND_TIMEOUT_SEC,
                    )
                    if trace_ctx is not None:
                        trace_ctx.set_reply(plain)
                    return
                except Exception:
                    pass
            parse_mode = None
            text = _strip_html(text)
        except (TimedOut, NetworkError, asyncio.TimeoutError) as exc:
            logger.warning("Telegram reply_text failed (%s: %s); falling back to background send", type(exc).__name__, exc)
        except Exception:
            logger.exception("Telegram reply_text failed with unexpected error; falling back to background send")

    chat_id = int(update.effective_chat.id)
    reply_to = int(update.message.message_id) if update.message else None
    if trace_ctx is not None:
        trace_ctx.set_reply(text)
    asyncio.create_task(
        _send_or_notify(
            bot=context.bot,
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            reply_to_message_id=reply_to,
        )
    )
