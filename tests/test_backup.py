import shutil
import time
import pytest
from pathlib import Path
from versioning.backup import BackupManager

SAMPLE_FILE = Path("example/budget_example.xlsx")


@pytest.fixture
def manager(tmp_path):
    src = tmp_path / "budget.xlsx"
    shutil.copy(SAMPLE_FILE, src)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    return BackupManager(str(src), str(backup_dir), keep=5)


def test_create_backup_makes_file(manager, tmp_path):
    path = manager.create()
    assert Path(path).exists()
    assert Path(path).suffix == ".xlsx"


def test_list_backups_returns_newest_first(manager):
    manager.create()
    time.sleep(0.02)
    manager.create()
    backups = manager.list_backups()
    assert len(backups) == 2
    assert backups[0]["index"] == 1  # index 1 = most recent


def test_keep_limit_prunes_oldest(manager):
    for _ in range(7):
        manager.create()
        time.sleep(0.02)
    backups = manager.list_backups()
    assert len(backups) == 5


def test_restore_replaces_source(manager, tmp_path):
    original_size = Path(manager.source_path).stat().st_size
    manager.create()
    # Corrupt source
    Path(manager.source_path).write_bytes(b"corrupted")
    manager.restore(1)
    assert Path(manager.source_path).stat().st_size == original_size
