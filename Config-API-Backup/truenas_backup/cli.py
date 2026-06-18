"""
truenas_backup.cli
~~~~~~~~~~~~~~~~~~
Command-line interface for truenas-config-backup.

This module owns all CLI logic:
  - argument parsing
  - config loading and validation
  - logging setup
  - client, manager, and notifier construction
  - exit codes

Entry points:
  Installed command:  truenas-backup   (via pyproject.toml [project.scripts])
  Direct invocation:  python run_backup.py  (thin shim at project root)

Usage:
    truenas-backup [--dry-run] [--config PATH] [--log-level LEVEL]
"""

from __future__ import annotations

import argparse
import sys

from .backup import BackupManager
from .client import TrueNASBackupClient
from .config import Config, build_config
from .notifier import Notifier
from .utils import setup_logging


def build_client_from_config(cfg: Config) -> TrueNASBackupClient:
    """Construct a :class:`TrueNASBackupClient` from a :class:`Config`.

    All five client settings are passed explicitly so that the Config layer
    is the single source of truth — no silent fallback to env vars inside
    the client constructor.
    """
    return TrueNASBackupClient(
        host=cfg.host,
        api_key=cfg.api_key,
        verify_ssl=cfg.verify_ssl,
        legacy_ws=cfg.legacy_ws,
        buffered_download=cfg.buffered_download,
    )


def build_notifier_from_config(cfg: Config) -> Notifier:
    """Construct a :class:`Notifier` from a :class:`Config`."""
    return Notifier(cfg)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="truenas-backup",
        description="Back up TrueNAS system configuration via the WebSocket JSON-RPC API.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate backup and pruning without writing or deleting anything.",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help="Path to .env file (default: .env in the current directory).",
    )
    parser.add_argument(
        "--log-level",
        metavar="LEVEL",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override LOG_LEVEL from configuration.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # -- config ---------------------------------------------------------------
    try:
        cfg = build_config(env_file=args.config)
        if args.log_level:
            cfg.log_level = args.log_level
        cfg.validate()
    except (ValueError, FileNotFoundError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(2)

    # -- logging --------------------------------------------------------------
    log = setup_logging(cfg.log_file, cfg.log_level)

    if args.dry_run:
        log.info("=== DRY-RUN MODE -- no files will be written or deleted ===")

    log.info("truenas-config-backup starting (host=%s)", cfg.host)

    # -- run ------------------------------------------------------------------
    notifier = build_notifier_from_config(cfg)
    success = False
    detail = ""

    try:
        client = build_client_from_config(cfg)
        manager = BackupManager(cfg, client, dry_run=args.dry_run)
        dest = manager.run()
        success = True
        detail = str(dest)
        log.info("Backup saved: %s", dest)
    except Exception as exc:
        detail = str(exc)
        log.error("Backup failed: %s", exc, exc_info=True)

    notifier.notify(success=success, detail=detail)

    sys.exit(0 if success else 1)