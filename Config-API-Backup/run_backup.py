#!/usr/bin/env python3
"""
run_backup.py
~~~~~~~~~~~~~
Thin compatibility shim for direct invocation and cron use.

All logic lives in truenas_backup/cli.py.

Usage (direct / cron):
    python run_backup.py [--dry-run] [--config PATH] [--log-level LEVEL]

Usage (installed command):
    truenas-backup [--dry-run] [--config PATH] [--log-level LEVEL]

Usage (systemd timer):
    see contrib/systemd/truenas-backup.timer
"""

from truenas_backup.cli import main

if __name__ == "__main__":
    main()