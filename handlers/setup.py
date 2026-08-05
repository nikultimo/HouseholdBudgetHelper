from __future__ import annotations

import asyncio
import base64
import csv
import os
import re
import tempfile
from dataclasses import asdict
from typing import TYPE_CHECKING

import openpyxl
from pydantic import BaseModel, Field
from telegram import Update
from telegram.ext import (
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from excel.setup import (
    MandatoryPaymentSetup,
    WorkbookSetupData,
    apply_workbook_setup,
)

if TYPE_CHECKING:
    from config import Config
    from excel.reader import ExcelReader
    from llm.client import LLMClient
    from yadisk.sync import YadiskSync


(
    NAME,
    ACCOUNT,
    CAPITAL,
    SALARY,
    PAY_DAYS,
    PAYMENT_MODEL,
    PERCENTAGES,
    PAYMENTS,
    CATEGORIES,
    ACCOUNTS,
    IMPORT,
    CONFIRM,
) = range(12)


class SetupImportExtraction(BaseModel):
    categories: list[str] = Field(default_factory=list)
    accounts: list[str] = Field(default_factory=list)
    mandatory_payments: list[dict] = Field(default_factory=list)


def build_setup_handler(
    *,
    cfg: "Config",
    reader: "ExcelReader",
    yadisk: "YadiskSync",
    llm_client: "LLMClient | None" = None,
) -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("setup", lambda u, c: cmd_setup(u, c, cfg=cfg))],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_name)],
            ACCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_account)],
            CAPITAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_capital)],
            SALARY: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_salary)],
            PAY_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_pay_days)],
            PAYMENT_MODEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_payment_model)],
            PERCENTAGES: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_percentages)],
            PAYMENTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_payments)],
            CATEGORIES: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_categories)],
            ACCOUNTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_accounts)],
            IMPORT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, setup_import_text),
                MessageHandler(filters.Document.ALL, setup_import_document),
                MessageHandler(filters.PHOTO, lambda u, c: setup_import_photo(u, c, cfg=cfg, llm_client=llm_client)),
            ],
            CONFIRM: [
                MessageHandler(
                    filters.Regex(re.compile(r"^(да|yes|y)$", re.I)),
                    lambda u, c: setup_confirm(u, c, cfg=cfg, reader=reader, yadisk=yadisk),
                ),
                MessageHandler(filters.TEXT & ~filters.COMMAND, setup_reject),
            ],
        },
        fallbacks=[CommandHandler("cancel", setup_cancel)],
        allow_reentry=True,
    )


async def cmd_setup(update: Update, context, *, cfg: "Config") -> int:
    if not cfg.allowed_users:
        await update.message.reply_text(
            "/setup доступен только когда в config.json задан allowed_users."
        )
        return ConversationHandler.END
    if update.effective_user.id not in cfg.allowed_users:
        await update.message.reply_text("⛔ У вас нет доступа к настройке бота.")
        return ConversationHandler.END
    context.user_data["setup"] = {}
    await update.message.reply_text("Введите имя пользователя для бюджета.")
    return NAME


async def setup_name(update: Update, context) -> int:
    context.user_data["setup"]["user_name"] = update.message.text.strip()
    await update.message.reply_text("Введите счёт по умолчанию.")
    return ACCOUNT


async def setup_account(update: Update, context) -> int:
    context.user_data["setup"]["default_account"] = update.message.text.strip()
    await update.message.reply_text("Введите текущий капитал числом, или 0.")
    return CAPITAL


async def setup_capital(update: Update, context) -> int:
    context.user_data["setup"]["initial_capital"] = _parse_amount(update.message.text)
    await update.message.reply_text("Введите месячную зарплату чистыми.")
    return SALARY


async def setup_salary(update: Update, context) -> int:
    context.user_data["setup"]["salary"] = _parse_amount(update.message.text)
    await update.message.reply_text("Введите дни выплат через запятую, например: 5,20.")
    return PAY_DAYS


async def setup_pay_days(update: Update, context) -> int:
    days = [int(x) for x in re.findall(r"\d+", update.message.text)]
    if len(days) != 2:
        await update.message.reply_text("Нужно два дня выплат, например: 5,20.")
        return PAY_DAYS
    context.user_data["setup"]["salary_pay_days"] = tuple(sorted(days))
    await update.message.reply_text(
        "Выберите модель зарплаты: working_days или fixed_percent."
    )
    return PAYMENT_MODEL


async def setup_payment_model(update: Update, context) -> int:
    model = update.message.text.strip()
    if model not in {"working_days", "fixed_percent"}:
        await update.message.reply_text("Введите working_days или fixed_percent.")
        return PAYMENT_MODEL
    context.user_data["setup"]["salary_payment_model"] = model
    if model == "fixed_percent":
        await update.message.reply_text("Введите проценты выплат, например: 50,50.")
        return PERCENTAGES
    context.user_data["setup"]["salary_payment_percentages"] = (0.5, 0.5)
    await update.message.reply_text("Введите обязательные платежи построчно: название; день; сумма. Или skip.")
    return PAYMENTS


async def setup_percentages(update: Update, context) -> int:
    nums = [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[,.]\d+)?", update.message.text)]
    if len(nums) != 2:
        await update.message.reply_text("Нужно два процента, например: 50,50.")
        return PERCENTAGES
    if max(nums) > 1:
        nums = [n / 100 for n in nums]
    if abs(sum(nums) - 1.0) > 0.0001:
        await update.message.reply_text("Проценты должны суммарно давать 100%.")
        return PERCENTAGES
    context.user_data["setup"]["salary_payment_percentages"] = (nums[0], nums[1])
    await update.message.reply_text("Введите обязательные платежи построчно: название; день; сумма. Или skip.")
    return PAYMENTS


async def setup_payments(update: Update, context) -> int:
    context.user_data["setup"]["mandatory_payments"] = _parse_payments(update.message.text)
    await update.message.reply_text("Введите категории через запятую. Или skip.")
    return CATEGORIES


async def setup_categories(update: Update, context) -> int:
    context.user_data["setup"]["categories"] = _parse_list(update.message.text)
    await update.message.reply_text("Введите счета через запятую. Или skip.")
    return ACCOUNTS


async def setup_accounts(update: Update, context) -> int:
    context.user_data["setup"]["accounts"] = _parse_list(update.message.text)
    await update.message.reply_text(
        "Можно вставить импортируемые данные текстом, отправить .txt/.csv/.xlsx или фото. "
        "Или напишите skip."
    )
    return IMPORT


async def setup_import_text(update: Update, context) -> int:
    _merge_import(context.user_data["setup"], update.message.text)
    await update.message.reply_text(_summary(context.user_data["setup"]) + "\n\nНапишите да для применения или /cancel.")
    return CONFIRM


async def setup_import_document(update: Update, context) -> int:
    doc = update.message.document
    name = doc.file_name or ""
    if not name.lower().endswith((".txt", ".csv", ".xlsx")):
        await update.message.reply_text("Поддерживаются только .txt, .csv и .xlsx.")
        return IMPORT
    tmp_path = tempfile.mktemp(suffix=os.path.splitext(name)[1])
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(tmp_path)
        imported_text = await asyncio.to_thread(_extract_import_text_from_file, tmp_path)
        _merge_import(context.user_data["setup"], imported_text)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    await update.message.reply_text(_summary(context.user_data["setup"]) + "\n\nНапишите да для применения или /cancel.")
    return CONFIRM


async def setup_import_photo(update: Update, context, *, cfg: "Config", llm_client: "LLMClient | None") -> int:
    if llm_client is None:
        await update.message.reply_text("Фото принято, но vision-модель недоступна для setup. Проверьте сводку.")
        await update.message.reply_text(_summary(context.user_data["setup"]) + "\n\nНапишите да для применения или /cancel.")
        return CONFIRM
    tmp_path = tempfile.mktemp(suffix=".jpg")
    try:
        photo = update.message.photo[-1]
        tg_file = await context.bot.get_file(photo.file_id)
        await tg_file.download_to_drive(tmp_path)
        image_b64 = await asyncio.to_thread(_read_b64, tmp_path)
        extracted = await llm_client.chat_vision(
            image_b64=image_b64,
            text_prompt=(
                "Extract only household budget setup data from this image. "
                "Return categories, accounts, and recurring mandatory payments "
                "with description, due_day, amount. Ignore transactions."
            ),
            response_model=SetupImportExtraction,
            vision_model=cfg.vision_model,
            span_name="setup_import_photo",
        )
        _merge_extracted_import(context.user_data["setup"], extracted)
    except Exception:
        await update.message.reply_text("Не удалось извлечь setup-данные из фото. Проверьте сводку.")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    await update.message.reply_text(_summary(context.user_data["setup"]) + "\n\nНапишите да для применения или /cancel.")
    return CONFIRM


async def setup_reject(update: Update, context) -> int:
    await update.message.reply_text("Настройка не применена. Напишите да или /cancel.")
    return CONFIRM


async def setup_confirm(update: Update, context, *, cfg: "Config", reader: "ExcelReader", yadisk: "YadiskSync") -> int:
    setup = _build_setup_data(context.user_data.get("setup", {}), cfg)
    await asyncio.to_thread(apply_workbook_setup, cfg.budget_file_path, setup)
    reader.invalidate_cache()
    try:
        await yadisk.upload()
        suffix = "Файл загружен на Яндекс Диск."
    except Exception:
        suffix = "Файл сохранён локально, но загрузка на Яндекс Диск не удалась."
    context.user_data.pop("setup", None)
    await update.message.reply_text(f"✅ Настройка завершена. {suffix}")
    return ConversationHandler.END


async def setup_cancel(update: Update, context) -> int:
    context.user_data.pop("setup", None)
    await update.message.reply_text("Настройка отменена.")
    return ConversationHandler.END


def _build_setup_data(data: dict, cfg: "Config") -> WorkbookSetupData:
    return WorkbookSetupData(
        user_name=data.get("user_name") or cfg.default_user,
        default_account=data.get("default_account") or cfg.default_account,
        initial_capital=float(data.get("initial_capital") or 0),
        salary=float(data.get("salary") or 0),
        salary_pay_days=tuple(data.get("salary_pay_days") or cfg.salary_pay_days),
        salary_payment_model=data.get("salary_payment_model") or cfg.salary_payment_model,
        salary_payment_percentages=tuple(
            data.get("salary_payment_percentages") or cfg.salary_payment_percentages
        ),
        categories=list(data.get("categories") or []),
        accounts=list(data.get("accounts") or [data.get("default_account") or cfg.default_account]),
        mandatory_payments=list(data.get("mandatory_payments") or []),
    )


def _parse_amount(text: str) -> float:
    cleaned = text.replace(" ", "").replace(",", ".")
    match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    return float(match.group(0)) if match else 0.0


def _parse_list(text: str) -> list[str]:
    if text.strip().lower() in {"skip", "нет", "-"}:
        return []
    return [p.strip() for p in re.split(r"[,;\n]", text) if p.strip()]


def _parse_payments(text: str) -> list[MandatoryPaymentSetup]:
    if text.strip().lower() in {"skip", "нет", "-"}:
        return []
    payments: list[MandatoryPaymentSetup] = []
    for line in text.splitlines():
        if ";" in line or "|" in line:
            parts = [p.strip() for p in re.split(r"[;|]", line) if p.strip()]
        else:
            parts = [p.strip() for p in next(csv.reader([line])) if p.strip()]
        if len(parts) >= 3:
            payments.append(
                MandatoryPaymentSetup(
                    description=parts[0],
                    due_day=int(_parse_amount(parts[1])),
                    amount=_parse_amount(parts[2]),
                )
            )
    return payments


def _extract_import_text_from_file(path: str) -> str:
    lower = path.lower()
    if lower.endswith((".txt", ".csv")):
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    if lower.endswith(".xlsx"):
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        lines: list[str] = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                values = [str(v) for v in row if v is not None]
                if values:
                    lines.append("; ".join(values))
        return "\n".join(lines)
    return ""


def _read_b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _merge_import(data: dict, text: str) -> None:
    if text.strip().lower() in {"skip", "нет", "-"}:
        return
    imported_payments = _parse_payments(text)
    if imported_payments:
        data.setdefault("mandatory_payments", []).extend(imported_payments)
    for line in text.splitlines():
        lower = line.lower()
        if lower.startswith(("categories:", "категории:")):
            data.setdefault("categories", []).extend(_parse_list(line.split(":", 1)[1]))
        elif lower.startswith(("accounts:", "счета:")):
            data.setdefault("accounts", []).extend(_parse_list(line.split(":", 1)[1]))


def _merge_extracted_import(data: dict, extracted: SetupImportExtraction) -> None:
    if extracted.categories:
        data.setdefault("categories", []).extend(c for c in extracted.categories if c)
    if extracted.accounts:
        data.setdefault("accounts", []).extend(a for a in extracted.accounts if a)
    for raw_payment in extracted.mandatory_payments:
        description = str(raw_payment.get("description") or "").strip()
        if not description:
            continue
        data.setdefault("mandatory_payments", []).append(
            MandatoryPaymentSetup(
                description=description,
                due_day=int(float(raw_payment.get("due_day") or 0)),
                amount=float(raw_payment.get("amount") or 0),
            )
        )


def _summary(data: dict) -> str:
    preview = _build_setup_data(data, _FallbackConfig())
    raw = asdict(preview)
    payments = raw.pop("mandatory_payments", [])
    return (
        "<b>Сводка setup</b>\n"
        f"Имя: {raw['user_name']}\n"
        f"Счёт: {raw['default_account']}\n"
        f"Капитал: {raw['initial_capital']:,.0f} ₽\n"
        f"Зарплата: {raw['salary']:,.0f} ₽\n"
        f"Дни выплат: {raw['salary_pay_days']}\n"
        f"Модель зарплаты: {raw['salary_payment_model']}\n"
        f"Платежей: {len(payments)}\n"
        f"Категорий: {len(raw['categories'])}\n"
        f"Счетов: {len(raw['accounts'])}"
    )


class _FallbackConfig:
    default_user = "User1"
    default_account = "Main Card"
    salary_pay_days = (5, 20)
    salary_payment_model = "working_days"
    salary_payment_percentages = (0.5, 0.5)
