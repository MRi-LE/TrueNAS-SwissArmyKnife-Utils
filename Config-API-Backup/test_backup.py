"""Tests for truenas_backup.backup -- tiers, filenames, retention sort, promotion."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from truenas_backup.backup import BackupManager, DAILY, WEEKLY, MONTHLY, _parse_filename_ts
from truenas_backup.client import SystemInfo
from truenas_backup.config import Config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_path) -> Config:
    cfg = Config(
        host="truenas.local",
        api_key="key",
        backup_dir=tmp_path / "backups",
        retain_daily=3,
        retain_weekly=2,
        retain_monthly=1,
    )
    cfg.__post_init__()
    return cfg


@pytest.fixture
def fake_info() -> SystemInfo:
    return SystemInfo(hostname="mynas", version="25.04")


@pytest.fixture
def client_mock(fake_info) -> MagicMock:
    m = MagicMock()
    m.get_system_info.return_value = fake_info

    def _download(dest, **kw):
        dest.touch()
        return 1024

    m.download_config.side_effect = _download
    return m


def make_manager(cfg, client, now: datetime, dry_run=False) -> BackupManager:
    mgr = BackupManager(cfg, client, dry_run=dry_run)
    mgr.set_now(now)
    return mgr


# ---------------------------------------------------------------------------
# _parse_filename_ts
# ---------------------------------------------------------------------------

def test_parse_filename_ts_happy_path(tmp_path):
    p = tmp_path / "truenas-mynas-25.04-20250511-020001.tar"
    assert _parse_filename_ts(p) == datetime(2025, 5, 11, 2, 0, 1)


def test_parse_filename_ts_malformed_returns_epoch(tmp_path):
    p = tmp_path / "notabackup.tar"
    assert _parse_filename_ts(p) == datetime(1970, 1, 1)


# ---------------------------------------------------------------------------
# Filename format
# ---------------------------------------------------------------------------

def test_filename_format(cfg, client_mock):
    now = datetime(2025, 5, 11, 2, 0, 1)  # Sunday
    mgr = make_manager(cfg, client_mock, now)
    mgr._download = lambda dest, info: (
        dest.parent.mkdir(parents=True, exist_ok=True) or dest.touch()
    )
    result = mgr.run()
    assert result.name == "truenas-mynas-25.04-20250511-020001.tar"


# ---------------------------------------------------------------------------
# Tier routing
# ---------------------------------------------------------------------------

def test_daily_only_on_weekday(cfg, client_mock):
    now = datetime(2025, 5, 7, 2, 0, 0)  # Wednesday
    mgr = make_manager(cfg, client_mock, now)
    mgr._download = lambda dest, info: (
        dest.parent.mkdir(parents=True, exist_ok=True), dest.touch()
    )

    mgr.run()

    assert len(list((cfg.backup_dir / DAILY).glob("*.tar"))) == 1
    assert len(list((cfg.backup_dir / WEEKLY).glob("*.tar"))) == 0
    assert len(list((cfg.backup_dir / MONTHLY).glob("*.tar"))) == 0


def test_weekly_promotion_on_sunday(cfg, client_mock):
    now = datetime(2025, 5, 11, 2, 0, 0)  # Sunday
    mgr = make_manager(cfg, client_mock, now)
    mgr._download = lambda dest, info: (
        dest.parent.mkdir(parents=True, exist_ok=True), dest.touch()
    )

    mgr.run()

    assert len(list((cfg.backup_dir / WEEKLY).glob("*.tar"))) == 1


def test_monthly_promotion_on_first(cfg, client_mock):
    now = datetime(2025, 5, 1, 2, 0, 0)  # 1st — also a Thursday
    mgr = make_manager(cfg, client_mock, now)
    mgr._download = lambda dest, info: (
        dest.parent.mkdir(parents=True, exist_ok=True), dest.touch()
    )

    mgr.run()

    assert len(list((cfg.backup_dir / MONTHLY).glob("*.tar"))) == 1


def test_sunday_and_first_promotes_to_both(cfg, client_mock):
    now = datetime(2025, 6, 1, 2, 0, 0)  # Sunday + 1st
    mgr = make_manager(cfg, client_mock, now)
    mgr._download = lambda dest, info: (
        dest.parent.mkdir(parents=True, exist_ok=True), dest.touch()
    )

    mgr.run()

    assert len(list((cfg.backup_dir / WEEKLY).glob("*.tar"))) == 1
    assert len(list((cfg.backup_dir / MONTHLY).glob("*.tar"))) == 1


# ---------------------------------------------------------------------------
# Retention / pruning
# ---------------------------------------------------------------------------

def test_daily_retention_prunes_oldest(cfg, client_mock):
    """With retain_daily=3, a 4th run should delete the oldest file."""
    daily_dir = cfg.backup_dir / DAILY
    daily_dir.mkdir(parents=True)

    # Pre-seed 3 older backups (timestamps are in the name, not just mtime)
    for i in range(1, 4):
        (daily_dir / f"truenas-mynas-25.04-2025050{i}-020000.tar").touch()

    now = datetime(2025, 5, 7, 2, 0, 0)  # Wednesday
    mgr = make_manager(cfg, client_mock, now)
    mgr._download = lambda dest, info: dest.touch()

    mgr.run()

    remaining = sorted(daily_dir.glob("*.tar"))
    assert len(remaining) == 3  # retain_daily=3
    # The pre-seeded May 1 file should have been pruned
    names = [f.name for f in remaining]
    assert not any("20250501" in n for n in names)


def test_retention_sorts_by_filename_timestamp_not_mtime(cfg, client_mock, tmp_path):
    """Pruning must use the embedded filename timestamp, not filesystem mtime."""
    daily_dir = cfg.backup_dir / DAILY
    daily_dir.mkdir(parents=True)

    # Pre-seed 3 files so that adding one more (via mgr.run) triggers pruning (retain=3).
    # Crucially: create old_file LAST so its mtime is the newest — a sort-by-mtime
    # implementation would prune new_file instead of old_file.
    new_file  = daily_dir / "truenas-mynas-25.04-20250501-020000.tar"
    mid_file  = daily_dir / "truenas-mynas-25.04-20250301-020000.tar"
    old_file  = daily_dir / "truenas-mynas-25.04-20250101-020000.tar"
    new_file.touch()
    mid_file.touch()
    old_file.touch()  # newest mtime, but oldest filename timestamp

    now = datetime(2025, 5, 7, 2, 0, 0)  # Wednesday
    mgr = make_manager(cfg, client_mock, now)
    mgr._download = lambda dest, info: dest.touch()

    mgr.run()  # adds one more: 4 total, prune 1 (oldest by filename = Jan 1)

    remaining = [f.name for f in daily_dir.glob("*.tar")]
    assert len(remaining) == 3
    # The Jan 1 file (oldest by filename) must be pruned, not the May 1 file
    assert not any("20250101" in n for n in remaining)
    assert any("20250501" in n for n in remaining)


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------

def test_dry_run_does_not_create_files(cfg, client_mock):
    now = datetime(2025, 5, 11, 2, 0, 0)
    mgr = make_manager(cfg, client_mock, now, dry_run=True)
    mgr.run()

    assert not (cfg.backup_dir / DAILY).exists()
    assert not (cfg.backup_dir / WEEKLY).exists()
    assert not (cfg.backup_dir / MONTHLY).exists()
    client_mock.download_config.assert_not_called()


def test_dry_run_does_not_prune_existing_files(cfg, client_mock):
    """Dry-run must not delete files that exceed retention."""
    daily_dir = cfg.backup_dir / DAILY
    daily_dir.mkdir(parents=True)
    for i in range(1, 6):
        (daily_dir / f"truenas-mynas-25.04-2025050{i}-020000.tar").touch()

    now = datetime(2025, 5, 11, 2, 0, 0)
    mgr = make_manager(cfg, client_mock, now, dry_run=True)
    mgr.run()

    # All 5 files must still be there
    assert len(list(daily_dir.glob("*.tar"))) == 5


# ---------------------------------------------------------------------------
# ensure_dirs — directory creation
# ---------------------------------------------------------------------------

def test_ensure_dirs_creates_tier_subdirs_when_backup_dir_exists(cfg, client_mock):
    """If BACKUP_DIR exists but tier dirs don't, they must be created on first run."""
    cfg.backup_dir.mkdir(parents=True)
    # Tier dirs must not exist yet
    assert not (cfg.backup_dir / "daily").exists()
    assert not (cfg.backup_dir / "weekly").exists()
    assert not (cfg.backup_dir / "monthly").exists()

    now = datetime(2025, 5, 7, 2, 0, 0)  # Wednesday — only daily
    mgr = make_manager(cfg, client_mock, now)
    mgr.run()

    assert (cfg.backup_dir / "daily").is_dir()
    assert (cfg.backup_dir / "weekly").is_dir()
    assert (cfg.backup_dir / "monthly").is_dir()


def test_ensure_dirs_creates_backup_dir_and_tiers_from_scratch(cfg, client_mock):
    """BACKUP_DIR does not exist at all — full tree must be created."""
    assert not cfg.backup_dir.exists()

    now = datetime(2025, 5, 7, 2, 0, 0)
    mgr = make_manager(cfg, client_mock, now)
    mgr.run()

    assert (cfg.backup_dir / "daily").is_dir()
    assert (cfg.backup_dir / "weekly").is_dir()
    assert (cfg.backup_dir / "monthly").is_dir()
