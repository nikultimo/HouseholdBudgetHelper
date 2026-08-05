#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from excel.setup import validate_template_workbook
from yadisk.sync import YadiskSync


REMOTE_TEMPLATE_PATH = "/example_budget.xlsx"
LOCAL_TEMPLATE_PATH = Path("example/budget_example.xlsx")


async def _main() -> None:
    load_dotenv()
    token = os.environ["YADISK_TOKEN"]
    tmp_path = LOCAL_TEMPLATE_PATH.with_suffix(".download.tmp.xlsx")
    sync = YadiskSync(
        token=token,
        remote_path=REMOTE_TEMPLATE_PATH,
        local_path=str(tmp_path),
    )
    await sync.download()
    validate_template_workbook(tmp_path)
    tmp_path.replace(LOCAL_TEMPLATE_PATH)
    print(f"Updated {LOCAL_TEMPLATE_PATH} from Yandex Disk {REMOTE_TEMPLATE_PATH}")


if __name__ == "__main__":
    asyncio.run(_main())
