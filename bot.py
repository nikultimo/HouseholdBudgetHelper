from __future__ import annotations
import asyncio
import logging
import os
import re
import time
from collections import defaultdict, deque
from datetime import datetime

from telegram import Update, CallbackQuery
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    TypeHandler,
    filters,
)
from telegram.request import HTTPXRequest

from config import load_config
from excel.reader import ExcelReader
from excel.setup import ensure_runtime_workbook
from excel.ledger import ensure_ledger_schema, needs_ledger_migration, validate_ledger_workbook
from excel.writer import ExcelWriter
from versioning.backup import BackupManager
from yadisk.sync import YadiskSync
from llm.client import LLMClient
from llm.tracing import start_trace
from handlers.transaction import download_and_transcribe
from handlers import admin as admin_handlers
from llm.tips_loader import append_tip
from handlers.salary import cmd_salary
from handlers.capital import cmd_update_capital
from llm.router import classify_intent
from handlers.photo import handle_photo_receipt
from handlers.stats import cmd_stats
from handlers.setup import build_setup_handler
from handlers.edit import (
    apply_field_value,
    build_delete_keyboard,
    build_edit_keyboard,
    clear_pending_edit,
    find_transaction,
    format_field_value,
    format_transaction_state,
    get_field_label,
    get_pending_edit,
    set_pending_edit,
)
from dispatcher import dispatch
from telegram_helpers import reply_to_update, _edit_callback_message_with_retries

logger = logging.getLogger(__name__)

cfg = None
llm_client = None
reader = None
writer = None
backup_manager = None
yadisk = None

_pending: dict[str, tuple[object, float]] = {}
_excel_lock = asyncio.Lock()

_PENDING_TTL_SEC = 600.0  # 10 minutes
_rate_windows: dict[str, deque[float]] = defaultdict(deque)
_RATE_LIMIT = 20
_RATE_WINDOW = 60.0

_CONCURRENT_UPDATES = int(os.getenv("CONCURRENT_UPDATES", "1"))


def _check_rate_limit(user_id: str) -> bool:
    now = time.monotonic()
    dq = _rate_windows[user_id]
    while dq and now - dq[0] > _RATE_WINDOW:
        dq.popleft()
    if len(dq) >= _RATE_LIMIT:
        return False
    dq.append(now)
    return True


class _SecretRedactionFilter(logging.Filter):
    _telegram_token_re = re.compile(r"bot\d+:[A-Za-z0-9_-]{20,}")
    _bearer_re = re.compile(r"(Bearer\s+)[A-Za-z0-9._-]{10,}")

    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            original = record.getMessage()
        except Exception:
            return True

        redacted = self._telegram_token_re.sub("bot<redacted>", original)
        redacted = self._bearer_re.sub(r"\1<redacted>", redacted)
        for secret in self._secrets:
            redacted = redacted.replace(secret, "<redacted>")

        if redacted != original:
            record.msg = redacted
            record.args = ()
        return True


def configure_logging() -> None:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    deps_log_level = os.getenv("DEPS_LOG_LEVEL", "WARNING").upper()

    logging.basicConfig(
        level=log_level,
        format="%(levelname)s:%(name)s:%(message)s",
        force=True,
    )

    for logger_name in (
        "httpx",
        "httpcore",
        "telegram",
        "telegram.ext",
        "openai",
    ):
        logging.getLogger(logger_name).setLevel(deps_log_level)

    secrets = [
        os.getenv("TELEGRAM_TOKEN"),
        os.getenv("OPENROUTER_API_KEY"),
        os.getenv("YADISK_TOKEN"),
        os.getenv("WHISPER_API_KEY"),
        os.getenv("LANGFUSE_PUBLIC_KEY"),
        os.getenv("LANGFUSE_SECRET_KEY"),
    ]
    redaction_filter = _SecretRedactionFilter(secrets=secrets)
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.addFilter(redaction_filter)


def _is_allowed(user_id: int) -> bool:
    return cfg is not None and user_id in cfg.allowed_users


async def enforce_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Silently stop every update that does not belong to an allowed user."""
    del context
    user = update.effective_user
    if user is None or not _is_allowed(user.id):
        logger.warning("Ignored unauthorized Telegram update")
        raise ApplicationHandlerStop


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_user.id):
        await reply_to_update(update, context, "⛔ У вас нет доступа к этому боту.")
        return
    user_id = str(update.effective_user.id)
    if not _check_rate_limit(user_id):
        await reply_to_update(update, context, "⏳ Слишком много запросов. Подожди немного.")
        return
    msg_obj = update.effective_message
    if msg_obj is None or not msg_obj.text:
        return
    text = msg_obj.text.strip()
    today = datetime.now().strftime("%Y-%m-%d")

    # If user is editing one field, apply the typed value and return to the field menu.
    edit_entry = get_pending_edit(user_id)
    if edit_entry and edit_entry.get("mode") == "edit" and edit_entry.get("field"):
        ok, status = apply_field_value(edit_entry, text)
        set_pending_edit(user_id, edit_entry)
        next_step = "Выбери следующее поле или сохрани." if ok else "Отправь новое значение для выбранного поля."
        msg = f"{status}\n\n{format_transaction_state(edit_entry['txn'])}\n\n{next_step}"
        await reply_to_update(
            update,
            context,
            msg,
            parse_mode="HTML",
            reply_markup=build_edit_keyboard(),
        )
        return

    try:
        await asyncio.wait_for(msg_obj.chat.send_action("typing"), timeout=3)
    except Exception:
        pass
    trace_ctx = start_trace(user_id=user_id, input_text=text)
    try:
        t0 = time.monotonic()
        try:
            classification = await classify_intent(text, llm_client, cfg.router_model, trace_ctx=trace_ctx)
        except Exception as exc:
            logger.warning("classify_intent failed: %s", type(exc).__name__)
            await reply_to_update(
                update,
                context,
                "⚠️ Не удалось обработать сообщение (ошибка связи с моделью). Попробуй ещё раз через пару секунд.",
            )
            return
        logger.info("classify_intent took %.2fs → %s", time.monotonic() - t0, classification.intent)
        trace_ctx.set_metadata("intent", classification.intent)
        t1 = time.monotonic()
        await dispatch(
            update, context, classification, text, user_id, today, trace_ctx,
            cfg=cfg, reader=reader, writer=writer, yadisk=yadisk,
            llm_client=llm_client, pending=_pending,
        )
        logger.info("dispatch took %.2fs", time.monotonic() - t1)
    finally:
        trace_ctx.finish()


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_user.id):
        await reply_to_update(update, context, "⛔ У вас нет доступа к этому боту.")
        return
    if not _check_rate_limit(str(update.effective_user.id)):
        await reply_to_update(update, context, "⏳ Слишком много запросов. Подожди немного.")
        return
    if not getattr(cfg, "whisper_api_key", ""):
        await reply_to_update(
            update,
            context,
            "🎙 Голосовые отключены: не настроен WHISPER_API_KEY. "
            "Добавь его в `.env`, чтобы включить транскрипцию.",
        )
        return
    user_id = str(update.effective_user.id)
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        await asyncio.wait_for(update.message.chat.send_action("typing"), timeout=3)
    except Exception:
        pass
    try:
        text = await download_and_transcribe(
            voice=update.message.voice,
            bot=context.bot,
            llm_client=llm_client,
        )
    except Exception:
        await reply_to_update(update, context, "Не удалось распознать голосовое сообщение, попробуй ещё раз.")
        return
    await reply_to_update(update, context, f"🎙 Распознано: <i>{text}</i>", parse_mode="HTML")
    trace_ctx = start_trace(user_id=user_id, input_text=text)
    try:
        classification = await classify_intent(text, llm_client, cfg.router_model, trace_ctx=trace_ctx)
        trace_ctx.set_metadata("intent", classification.intent)
        await dispatch(
            update, context, classification, text, user_id, today, trace_ctx,
            cfg=cfg, reader=reader, writer=writer, yadisk=yadisk,
            llm_client=llm_client, pending=_pending,
        )
    finally:
        trace_ctx.finish()


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_user.id):
        await reply_to_update(update, context, "⛔ У вас нет доступа к этому боту.")
        return
    if not _check_rate_limit(str(update.effective_user.id)):
        await reply_to_update(update, context, "⏳ Слишком много запросов. Подожди немного.")
        return
    await handle_photo_receipt(update, context, llm_client=llm_client, reader=reader, cfg=cfg, pending=_pending)


async def _cleanup_pending_loop() -> None:
    """Periodically remove expired pending confirmation entries."""
    while True:
        await asyncio.sleep(300)
        now = time.monotonic()
        expired = [tok for tok, (_, ts) in list(_pending.items()) if now - ts > _PENDING_TTL_SEC]
        for tok in expired:
            _pending.pop(tok, None)
        if expired:
            logger.debug("Cleaned up %d expired pending token(s)", len(expired))


async def _upload_or_notify(query: CallbackQuery, yadisk: YadiskSync) -> None:
    try:
        await yadisk.upload()
    except Exception as exc:
        logger.error("Background upload failed: %s", exc)
        try:
            await asyncio.wait_for(
                query.message.reply_text(
                    "⚠️ Транзакция сохранена локально, но не удалось загрузить на Яндекс Диск. "
                    "Используй /sync для повторной попытки."
                ),
                timeout=12,
            )
        except Exception:
            pass


def _unpack_pending_entry(entry):
    if isinstance(entry, tuple) and len(entry) == 2:
        return entry
    return entry, time.monotonic()


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_user.id):
        await update.callback_query.answer("⛔ Нет доступа.")
        return
    query = update.callback_query
    try:
        await asyncio.wait_for(query.answer(), timeout=3)
    except Exception:
        pass
    parts = query.data.split(":", 2)
    action = parts[0]
    token = parts[1] if len(parts) > 1 else ""

    if action == "edit_cancel":
        user_id = str(update.effective_user.id)
        clear_pending_edit(user_id)
        edited = await _edit_callback_message_with_retries(query, "Редактирование отменено.", max_attempts=1)
        if not edited:
            asyncio.create_task(_edit_callback_message_with_retries(query, "Редактирование отменено."))
        return

    if action == "edit_select":
        entry_id = token
        user_id = str(update.effective_user.id)
        txn = find_transaction(reader, entry_id)
        if not txn:
            await _edit_callback_message_with_retries(query, "Транзакция не найдена.", max_attempts=1)
            return
        set_pending_edit(user_id, {
            "mode": "edit",
            "entry_id": txn.get("entry_id") or txn.get("row"),
            "row": txn.get("row"),
            "txn": txn,
            "field": None,
        })
        text = format_transaction_state(txn) + "\n\nВыбери поле для изменения."
        edited = await _edit_callback_message_with_retries(
            query, text, parse_mode="HTML", reply_markup=build_edit_keyboard(), max_attempts=1
        )
        if not edited:
            asyncio.create_task(_edit_callback_message_with_retries(
                query, text, parse_mode="HTML", reply_markup=build_edit_keyboard()
            ))
        return

    if action == "edit_field":
        user_id = str(update.effective_user.id)
        entry = get_pending_edit(user_id)
        if not entry:
            await _edit_callback_message_with_retries(query, "Редактирование уже завершено.", max_attempts=1)
            return
        entry["field"] = token
        set_pending_edit(user_id, entry)
        label = get_field_label(token)
        current_value = format_field_value(token, entry["txn"].get(token))
        text = (
            f"{format_transaction_state(entry['txn'])}\n\n"
            f"Редактируется: <b>{label}</b>\n"
            f"Текущее значение: <b>{current_value}</b>\n\n"
            "Отправь новое значение."
        )
        edited = await _edit_callback_message_with_retries(query, text, parse_mode="HTML", max_attempts=1)
        if not edited:
            asyncio.create_task(_edit_callback_message_with_retries(query, text, parse_mode="HTML"))
        return

    if action == "edit_save":
        user_id = str(update.effective_user.id)
        entry = get_pending_edit(user_id)
        if not entry:
            await _edit_callback_message_with_retries(query, "Редактирование уже завершено.", max_attempts=1)
            return
        txn = entry["txn"]
        entry_id = str(entry.get("entry_id") or entry["row"])
        async with _excel_lock:
            await asyncio.to_thread(backup_manager.create)
            try:
                await asyncio.to_thread(
                    writer.correct_transaction,
                    entry_id,
                    txn["date"], txn["description"], txn["category"], txn["type"],
                    txn["whose"], txn["amount"], txn["account"], txn["mandatory"],
                )
            except ValueError:
                reader.invalidate_cache()
                clear_pending_edit(user_id)
                already_text = "Эта транзакция уже была изменена или удалена."
                edited = await _edit_callback_message_with_retries(query, already_text, max_attempts=1)
                if not edited:
                    asyncio.create_task(_edit_callback_message_with_retries(query, already_text))
                return
            reader.invalidate_cache()
        clear_pending_edit(user_id)
        confirmation_text = f"✅ <b>Обновлено:</b> {txn['description']} · <b>{float(txn['amount']):,.0f} ₽</b>"
        edited = await _edit_callback_message_with_retries(query, confirmation_text, parse_mode="HTML", max_attempts=1)
        if not edited:
            asyncio.create_task(_edit_callback_message_with_retries(query, confirmation_text, parse_mode="HTML"))
        asyncio.create_task(_upload_or_notify(query, yadisk))
        return

    if action == "delete_cancel":
        edited = await _edit_callback_message_with_retries(query, "Удаление отменено.", max_attempts=1)
        if not edited:
            asyncio.create_task(_edit_callback_message_with_retries(query, "Удаление отменено."))
        return

    if action == "delete_select":
        entry_id = token
        txn = find_transaction(reader, entry_id)
        if not txn:
            await _edit_callback_message_with_retries(query, "Транзакция не найдена.", max_attempts=1)
            return
        text = format_transaction_state(txn) + "\n\nУдалить эту транзакцию?"
        edited = await _edit_callback_message_with_retries(
            query, text, parse_mode="HTML",
            reply_markup=build_delete_keyboard(str(txn.get("entry_id") or txn.get("row"))),
            max_attempts=1,
        )
        if not edited:
            asyncio.create_task(_edit_callback_message_with_retries(
                query, text, parse_mode="HTML",
                reply_markup=build_delete_keyboard(str(txn.get("entry_id") or txn.get("row")))
            ))
        return

    if action == "delete_confirm":
        entry_id = token
        async with _excel_lock:
            await asyncio.to_thread(backup_manager.create)
            try:
                await asyncio.to_thread(writer.reverse_transaction, entry_id)
            except ValueError:
                reader.invalidate_cache()
                already_text = "Эта транзакция уже была удалена."
                edited = await _edit_callback_message_with_retries(query, already_text, max_attempts=1)
                if not edited:
                    asyncio.create_task(_edit_callback_message_with_retries(query, already_text))
                return
            reader.invalidate_cache()
        confirmation_text = "🗑 Транзакция удалена."
        edited = await _edit_callback_message_with_retries(query, confirmation_text, max_attempts=1)
        if not edited:
            asyncio.create_task(_edit_callback_message_with_retries(query, confirmation_text))
        asyncio.create_task(_upload_or_notify(query, yadisk))
        return

    if action == "cancel":
        _pending.pop(token, None)
        edited = await _edit_callback_message_with_retries(query, "Отменено.", max_attempts=1)
        if not edited:
            asyncio.create_task(_edit_callback_message_with_retries(query, "Отменено."))
        return

    entry = _pending.pop(token, None)
    if not entry:
        edited = await _edit_callback_message_with_retries(query, "Транзакция уже была обработана.", max_attempts=1)
        if not edited:
            asyncio.create_task(_edit_callback_message_with_retries(query, "Транзакция уже была обработана."))
        return

    txn, created_at = _unpack_pending_entry(entry)
    if time.monotonic() - created_at > _PENDING_TTL_SEC:
        edited = await _edit_callback_message_with_retries(query, "⏰ Срок подтверждения истёк. Отправь транзакцию заново.", max_attempts=1)
        if not edited:
            asyncio.create_task(_edit_callback_message_with_retries(query, "⏰ Срок подтверждения истёк. Отправь транзакцию заново."))
        return

    async with _excel_lock:
        await asyncio.to_thread(backup_manager.create)
        capital_delta = txn.amount if txn.type == "Доход" else -txn.amount
        await asyncio.to_thread(
            writer.append_transaction_and_adjust_capital,
            txn_date=txn.date,
            description=txn.description,
            category=txn.category,
            txn_type=txn.type,
            whose=txn.whose,
            amount=txn.amount,
            account=txn.account,
            mandatory=txn.mandatory,
            capital_month=txn.date.strftime("%Y-%m"),
            capital_delta=capital_delta,
        )
        reader.invalidate_cache()
    confirmation_text = f"✅ <b>Добавлено:</b> {txn.description} · <b>{txn.amount:,.0f} ₽</b>"
    edited = await _edit_callback_message_with_retries(query, confirmation_text, parse_mode="HTML", max_attempts=1)
    if not edited:
        asyncio.create_task(_edit_callback_message_with_retries(query, confirmation_text, parse_mode="HTML"))
    asyncio.create_task(_upload_or_notify(query, yadisk))


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_to_update(
        update,
        context,
        "Привет! Я бот для учёта личного бюджета. Вот что я умею:\n\n"
        "<b>💸 Транзакции</b>\n"
        "Просто напиши или надиктуй:\n"
        "  <i>купил кофе 200р</i>\n"
        "  <i>бензин 4500</i>\n"
        "Сумму указывай цифрами; без неё я попрошу уточнение.\n"
        "Или отправь фото чека — распознаю автоматически.\n"
        "Можно найти, изменить или удалить последние транзакции через inline-кнопки.\n\n"
        "<b>📊 Аналитика и вопросы</b>\n"
        "  <i>сколько потратил на еду в апреле?</i>\n"
        "  <i>траты с 14 по 19 июня</i>\n"
        "  <i>сколько было денег 14 июня?</i>\n"
        "  <i>на что я трачу больше всего?</i>\n"
        "  <i>куда уходят деньги и сколько реально могу откладывать?</i>\n"
        "  <i>как снизить расходы?</i>\n\n"
        "<b>💰 Зарплата и капитал</b>\n"
        "  <i>какая у меня зарплата?</i>\n"
        "  <i>измени зарплату на 150000</i>\n"
        "  <i>сколько у меня денег / капитал?</i>\n"
        "  <i>баланс к декабрю?</i> — прогноз на начало месяца\n"
        "  <i>сверь капитал до 700000</i>\n\n"
        "<b>🏖 Отпуск</b>\n"
        "  <i>какая будет оценка отпускных с 10 по 19 июля?</i>\n"
        "Расчёт приблизительный и показывает использованные ограничения.\n\n"
        "<b>📅 Платежи</b>\n"
        "  <i>что нужно оплатить?</i> — чеклист предстоящих платежей\n\n"
        "<b>🎙 Голос</b>\n"
        "Отправь голосовое — транскрибирую и обработаю как текст.\n"
        "<i>(нужно настроить WHISPER_API_KEY)</i>\n\n"
        "<b>Команды:</b>\n"
        "/ask &lt;вопрос&gt; — аналитика напрямую\n"
        "/stats [YYYY-MM] — статистика расходов по категориям\n"
        "/salary [5|20] — расчёт ближайшей выплаты\n"
        "/update_capital — пересчитать 📈 Капитал на 7 месяцев\n"
        "/setup — мастер первичной настройки Excel-файла\n"
        "/last [N] — последние N транзакций\n"
        "/sync — обновить локальный файл с Яндекс.Диска\n"
        "/download_excel — прислать Excel-файл из Яндекс.Диска\n"
        "/versions — список бэкапов\n"
        "/restore N — восстановить бэкап\n"
        "/model &lt;id&gt; — сменить модель LLM",
        parse_mode="HTML",
    )


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from llm.agent import run_budget_agent
    from llm.tips_loader import load_tips
    question = " ".join(context.args) if context.args else ""
    if not question:
        await reply_to_update(update, context, "Использование: /ask <вопрос>")
        return
    try:
        await asyncio.wait_for(update.message.chat.send_action("typing"), timeout=3)
    except Exception:
        pass
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        answer = await run_budget_agent(
            question=question,
            reader=reader,
            llm_client=llm_client,
            tips=load_tips(),
            today=today,
            default_user=cfg.default_user,
            model=cfg.model,
            enable_code_tool=cfg.enable_unknown_code_executor,
        )
    except Exception:
        answer = "⚠️ Не удалось получить ответ от модели (таймаут/сеть). Попробуй ещё раз."
    await reply_to_update(update, context, answer, parse_mode="HTML")


async def cmd_tip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tip_text = " ".join(context.args).strip() if context.args else ""
    if not tip_text:
        await reply_to_update(update, context, "Использование: /tip <текст подсказки для агента>")
        return
    append_tip(tip_text)
    await reply_to_update(update, context, "✅ Подсказка сохранена.")


async def post_init(application: Application) -> None:
    global cfg, llm_client, reader, writer, backup_manager, yadisk
    if cfg is None:
        raise RuntimeError("Configuration must be loaded before Telegram startup")
    os.makedirs(os.path.dirname(os.path.abspath(cfg.budget_file_path)), exist_ok=True)
    yadisk = YadiskSync(
        token=cfg.yadisk_token,
        remote_path=cfg.yadisk_path,
        local_path=cfg.budget_file_path,
    )
    logger.info("post_init: downloading budget from Yandex Disk (remote=%s)", cfg.yadisk_path)
    try:
        await yadisk.download()
        logger.info("post_init: budget download complete (local=%s)", cfg.budget_file_path)
    except Exception as exc:
        if os.path.exists(cfg.budget_file_path):
            logger.warning(
                "post_init: Yandex Disk download failed (%s); continuing with local file: %s",
                type(exc).__name__,
                cfg.budget_file_path,
            )
        else:
            logger.warning(
                "post_init: Yandex Disk download failed (%s); creating local workbook from public template: %s",
                type(exc).__name__,
                cfg.budget_file_path,
            )
            await asyncio.to_thread(ensure_runtime_workbook, cfg.budget_file_path)
    reader = ExcelReader(cfg.budget_file_path)
    writer = ExcelWriter(cfg.budget_file_path)
    backup_manager = BackupManager(
        source_path=cfg.budget_file_path,
        backup_dir="backups",
        keep=cfg.backup_keep,
    )
    if await asyncio.to_thread(needs_ledger_migration, cfg.budget_file_path):
        await asyncio.to_thread(backup_manager.create)
    migration = await asyncio.to_thread(ensure_ledger_schema, cfg.budget_file_path)
    integrity_errors = await asyncio.to_thread(validate_ledger_workbook, cfg.budget_file_path)
    if integrity_errors:
        raise RuntimeError("Ledger integrity check failed: " + "; ".join(integrity_errors))
    if migration.changed:
        logger.info(
            "Ledger migration complete: entries=%d opening_date=%s opening_balance=%s",
            migration.migrated_entries,
            migration.opening_date,
            migration.opening_balance,
        )
        await yadisk.upload()
    llm_client = LLMClient(
        api_key=cfg.openrouter_api_key,
        model=cfg.model,
        whisper_api_key=cfg.whisper_api_key,
        whisper_base_url=cfg.whisper_base_url,
    )
    try:
        from salary.calendar_parser import fetch_calendar
        import datetime
        fetch_calendar(datetime.date.today().year)
        logger.info("Production calendar cached")
    except Exception as exc:
        logger.warning("Could not prefetch production calendar: %s", exc)

    asyncio.create_task(_cleanup_pending_loop())
    logger.info("Bot initialized. Model: %s", cfg.model)


def main() -> None:
    global cfg
    from dotenv import load_dotenv
    load_dotenv()
    configure_logging()
    logger.info("Starting bot process (pid=%s)", os.getpid())
    cfg = load_config()
    token = cfg.telegram_token

    telegram_request = HTTPXRequest(
        read_timeout=float(os.getenv("TELEGRAM_READ_TIMEOUT_SEC", "10")),
        write_timeout=float(os.getenv("TELEGRAM_WRITE_TIMEOUT_SEC", "10")),
        connect_timeout=float(os.getenv("TELEGRAM_CONNECT_TIMEOUT_SEC", "5")),
        pool_timeout=float(os.getenv("TELEGRAM_POOL_TIMEOUT_SEC", "1")),
    )
    app = (
        Application.builder()
        .token(token)
        .request(telegram_request)
        .concurrent_updates(_CONCURRENT_UPDATES)
        .post_init(post_init)
        .build()
    )

    app.add_handler(TypeHandler(Update, enforce_access), group=-1)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(build_setup_handler(cfg=cfg, reader=reader, yadisk=yadisk, llm_client=llm_client))
    app.add_handler(CommandHandler("stats", lambda u, c: cmd_stats(u, c, reader=reader)))
    app.add_handler(CommandHandler("salary", lambda u, c: cmd_salary(
        u,
        c,
        reader=reader,
        default_user=cfg.default_user,
        pay_days=cfg.salary_pay_days,
        payment_model=cfg.salary_payment_model,
        payment_percentages=cfg.salary_payment_percentages,
    )))
    app.add_handler(CommandHandler("update_capital", lambda u, c: cmd_update_capital(
        u,
        c,
        reader=reader,
        writer=writer,
        yadisk=yadisk,
        default_user=cfg.default_user,
        pay_days=cfg.salary_pay_days,
        payment_model=cfg.salary_payment_model,
        payment_percentages=cfg.salary_payment_percentages,
    )))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(CommandHandler("tip", cmd_tip))
    app.add_handler(CommandHandler("last", lambda u, c: admin_handlers.cmd_last(u, c, reader=reader)))
    app.add_handler(CommandHandler("model", lambda u, c: admin_handlers.cmd_model(u, c, cfg=cfg, llm_client=llm_client)))
    app.add_handler(CommandHandler("versions", lambda u, c: admin_handlers.cmd_versions(u, c, backup_manager=backup_manager)))
    app.add_handler(CommandHandler("restore", lambda u, c: admin_handlers.cmd_restore(u, c, backup_manager=backup_manager, yadisk=yadisk, reader=reader)))
    app.add_handler(CommandHandler("sync", lambda u, c: admin_handlers.cmd_sync(u, c, yadisk=yadisk, reader=reader)))
    app.add_handler(CommandHandler("download_excel", lambda u, c: admin_handlers.cmd_download_excel(u, c, yadisk=yadisk, reader=reader)))
    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_error_handler(on_error)

    app.run_polling(bootstrap_retries=-1)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, (TimedOut, NetworkError)):
        logger.warning("Telegram network hiccup: %s", context.error)
    else:
        logger.error("Unhandled exception in update %s", update, exc_info=context.error)


if __name__ == "__main__":
    main()
