"""
truenas_backup.backup
~~~~~~~~~~~~~~~~~~~~~
BackupManager orchestrates the full backup lifecycle:

1.  Download the config from the TrueNAS WebSocket API -> daily/
2.  If today is Sunday   -> copy to weekly/
3.  If today is the 1st  -> copy to monthly/
4.  Prune each tier to its configured retention limit.

Retention sort: files are ordered by the YYYYMMDD-HHMMSS timestamp embedded
in their filename, NOT by filesystem mtime (which can lie after copies/rsync).

Atomic writes and careful error handling mean a failed download never
leaves a partial file in any tier directory.
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable

from .client import TrueNASBackupClient, SystemInfo
from .config import Config
from .utils import ensure_dirs

log = logging.getLogger(__name__)

# Backup tiers
DAILY = "daily"
WEEKLY = "weekly"
MONTHLY = "monthly"

# Filename timestamp pattern: truenas-<host>-<ver>-YYYYMMDD-HHMMSS.tar
_TS_RE = re.compile(r"-(\d{8}-\d{6})\.tar$")


def _parse_filename_ts(path: Path) -> datetime:
    """Extract and parse the YYYYMMDD-HHMMSS timestamp from a backup filename.

    Falls back to epoch so malformed files sort first (and get pruned first).
    """
    m = _TS_RE.search(path.name)
    if not m:
        return datetime(1970, 1, 1)
    try:
        return datetime.strptime(m.group(1), "%Y%m%d-%H%M%S")
    except ValueError:
        return datetime(1970, 1, 1)


class BackupManager:
    """Manages the three-tier backup lifecycle for a single TrueNAS host."""

    def __init__(
        self,
        cfg: Config,
        client: TrueNASBackupClient,
        dry_run: bool = False,
    ) -> None:
        self._cfg = cfg
        self._client = client
        self._dry_run = dry_run
        self._now = datetime.now()

        self._tier_dirs = {
            DAILY:   cfg.backup_dir / DAILY,
            WEEKLY:  cfg.backup_dir / WEEKLY,
            MONTHLY: cfg.backup_dir / MONTHLY,
        }
        self._retain = {
            DAILY:   cfg.retain_daily,
            WEEKLY:  cfg.retain_weekly,
            MONTHLY: cfg.retain_monthly,
        }

    # -- public entry point ---------------------------------------------------

    def run(self) -> Path:
        """Execute the full backup cycle.  Returns the path of the new daily file."""
        if not self._dry_run:
            ensure_dirs(*self._tier_dirs.values())

        info = self._client.get_system_info()
        filename = self._make_filename(info)

        daily_path = self._tier_dirs[DAILY] / filename
        log.info("Starting backup -> %s", daily_path)

        self._download(daily_path, info)

        self._promote(daily_path, WEEKLY,  self._is_weekly_day)
        self._promote(daily_path, MONTHLY, self._is_monthly_day)

        self._prune(DAILY)
        self._prune(WEEKLY)
        self._prune(MONTHLY)

        log.info("Backup cycle complete.")
        return daily_path

    # -- private helpers -------------------------------------------------------

    def _make_filename(self, info: SystemInfo) -> str:
        """Build a human-readable, sortable filename.

        Format: truenas-{hostname}-{version}-{YYYYMMDD}-{HHMMSS}.tar
        Whitespace and slashes in version strings are replaced with hyphens.
        """
        safe_host = info.hostname.replace(" ", "-").replace("/", "-")
        safe_ver = (
            info.version
            .replace(" ", "-")
            .replace("/", "-")
            .replace("\\", "-")
        )
        timestamp = self._now.strftime("%Y%m%d-%H%M%S")
        return f"truenas-{safe_host}-{safe_ver}-{timestamp}.tar"

    def _download(self, dest: Path, info: SystemInfo) -> None:
        if self._dry_run:
            log.info("[dry-run] Would download config to %s", dest)
            return
        size = self._client.download_config(
            dest,
            secret_seed=self._cfg.secret_seed,
            root_authorized_keys=self._cfg.root_authorized_keys,
        )
        mb = size / (1024 * 1024)
        log.info("Downloaded config: %.2f MB -> %s", mb, dest.name)

    def _promote(
        self, source: Path, tier: str, condition: Callable[[], bool]
    ) -> None:
        """Copy *source* into *tier* directory if *condition* is True."""
        if not condition():
            log.debug("Skipping %s promotion (condition not met)", tier)
            return
        dest = self._tier_dirs[tier] / source.name
        if self._dry_run:
            log.info("[dry-run] Would copy to %s: %s", tier, dest)
            return
        shutil.copy2(source, dest)
        log.info("Promoted to %s: %s", tier, dest.name)

    def _prune(self, tier: str) -> None:
        """Delete oldest backups in *tier* beyond the configured retain limit.

        Ordering is by the YYYYMMDD-HHMMSS timestamp in the filename.
        """
        tier_dir = self._tier_dirs[tier]
        retain = self._retain[tier]

        files = sorted(
            tier_dir.glob("truenas-*.tar"),
            key=_parse_filename_ts,
        )
        to_delete = files[: max(0, len(files) - retain)]

        if self._dry_run:
            for f in to_delete:
                log.info("[dry-run] Would delete %s/%s", tier, f.name)
            return

        for f in to_delete:
            log.info("Pruning %s/%s", tier, f.name)
            f.unlink()

    # -- day-of conditions ----------------------------------------------------

    def _is_weekly_day(self) -> bool:
        """True on Sundays (weekday == 6)."""
        return self._now.weekday() == 6

    def _is_monthly_day(self) -> bool:
        """True on the 1st of the month."""
        return self._now.day == 1

    # -- allow injection of a specific datetime for testing -------------------

    def set_now(self, dt: datetime) -> None:
        """Override the reference datetime (useful in tests)."""
        self._now = dt
