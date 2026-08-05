from __future__ import annotations
import asyncio
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes
    from excel.reader import ExcelReader
    from versioning.backup import BackupManager
    from yadisk.sync import YadiskSync
    from config import Config
    from llm.client import LLMClient


async def cmd_last(
    update: "Update",
    context: "ContextTypes.DEFAULT_TYPE",
    reader: "ExcelReader",
) -> None:
    args = context.args if context.args else []
    n = int(args[0]) if args and args[0].isdigit() else 5
    txns = await asyncio.to_thread(reader.get_last_transactions, n)
    if not txns:
        await update.message.reply_text("Транзакций не найдено.")
        return
    lines = []
    for t in txns:
        icon = "💸" if t["type"] == "Расход" else "💰"
        lines.append(
            f"{icon} {t['date'].strftime('%d.%m')} · {t['description']} · "
            f"<b>{t['amount']:,.0f} ₽</b> [{t['category']}]"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_model(
    update: "Update",
    context: "ContextTypes.DEFAULT_TYPE",
    cfg: "Config",
    llm_client: "LLMClient",
) -> None:
    if not context.args:
        await update.message.reply_text(
            f"Текущая модель: <code>{cfg.model}</code>", parse_mode="HTML"
        )
        return
    new_model = context.args[0]
    cfg.update_model(new_model)
    llm_client.update_model(new_model)
    await update.message.reply_text(
        f"Модель изменена на <code>{new_model}</code>", parse_mode="HTML"
    )


async def cmd_versions(
    update: "Update",
    context: "ContextTypes.DEFAULT_TYPE",
    backup_manager: "BackupManager",
) -> None:
    backups = backup_manager.list_backups()[:10]
    if not backups:
        await update.message.reply_text("Бэкапов нет.")
        return
    lines = [f"#{b['index']} — {b['timestamp']}" for b in backups]
    await update.message.reply_text(
        "Последние бэкапы:\n" + "\n".join(lines) + "\n\nИспользуй /restore N для восстановления."
    )


async def cmd_restore(
    update: "Update",
    context: "ContextTypes.DEFAULT_TYPE",
    backup_manager: "BackupManager",
    yadisk: "YadiskSync",
    reader: "ExcelReader | None" = None,
) -> None:
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Использование: /restore <номер>")
        return
    index = int(context.args[0])
    try:
        await asyncio.to_thread(backup_manager.restore, index)
        if reader:
            reader.invalidate_cache()
        await yadisk.upload()
        await update.message.reply_text(
            f"Восстановлен бэкап #{index}. Файл загружен на Яндекс Диск."
        )
    except ValueError as e:
        await update.message.reply_text(str(e))
    except Exception as e:
        await update.message.reply_text(f"Ошибка восстановления: {e}")


async def cmd_sync(
    update: "Update",
    context: "ContextTypes.DEFAULT_TYPE",
    yadisk: "YadiskSync",
    reader: "ExcelReader | None" = None,
) -> None:
    try:
        await yadisk.download()
        if reader:
            reader.invalidate_cache()
        await update.message.reply_text("Файл обновлён с Яндекс Диска.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка синхронизации: {e}")


async def cmd_download_excel(
    update: "Update",
    context: "ContextTypes.DEFAULT_TYPE",
    yadisk: "YadiskSync",
    reader: "ExcelReader | None" = None,
) -> None:
    try:
        await yadisk.download()
        if reader:
            reader.invalidate_cache()
        filename = os.path.basename(yadisk.local_path) or "budget.xlsx"
        file_data = await asyncio.to_thread(lambda: open(yadisk.local_path, "rb").read())
        await update.message.reply_document(
            document=file_data,
            filename=filename,
            caption="Excel-файл скачан с Яндекс Диска.",
        )
    except Exception as e:
        await update.message.reply_text(f"Ошибка скачивания Excel: {e}")
