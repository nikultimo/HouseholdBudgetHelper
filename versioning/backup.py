from __future__ import annotations
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


class BackupManager:
    def __init__(self, source_path: str, backup_dir: str, keep: int = 30) -> None:
        self.source_path = source_path
        self.backup_dir = Path(backup_dir)
        self.keep = keep
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def create(self) -> str:
        """Copy source to timestamped backup. Prune old backups. Return backup path."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        dest = self.backup_dir / f"budget_{ts}.xlsx"
        shutil.copy2(self.source_path, dest)
        self._prune()
        return str(dest)

    def list_backups(self) -> list[dict[str, Any]]:
        """Return backups newest-first with 1-based index."""
        files = sorted(
            self.backup_dir.glob("budget_*.xlsx"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return [
            {
                "index": i + 1,
                "path": str(f),
                "timestamp": datetime.fromtimestamp(f.stat().st_mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "name": f.name,
            }
            for i, f in enumerate(files)
        ]

    def restore(self, index: int) -> str:
        """Restore backup at 1-based index to source_path. Returns backup path used."""
        backups = self.list_backups()
        if index < 1 or index > len(backups):
            raise ValueError(f"Invalid backup index {index}. Available: 1-{len(backups)}")
        backup_path = backups[index - 1]["path"]
        shutil.copy2(backup_path, self.source_path)
        return backup_path

    def _prune(self) -> None:
        backups = self.list_backups()
        for old in backups[self.keep:]:
            Path(old["path"]).unlink(missing_ok=True)
