"""
truenas_backup.config
~~~~~~~~~~~~~~~~~~~~~
Configuration dataclass populated from environment variables or a .env file.

No third-party library is used: the .env parser is a minimal implementation
that handles comments, blank lines, and quoted values.

Inline comments (KEY=value  # comment) are NOT supported to avoid ambiguity
with values that legitimately contain '#'. Use comments on their own lines.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path


# ── .env parser ───────────────────────────────────────────────────────────────

_QUOTE_RE = re.compile(r'^(["\'])(.*)(\1)$', re.DOTALL)


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file into a dict.

    Rules:
    - Lines starting with ``#`` (after optional whitespace) are comments.
    - Blank lines are ignored.
    - Values may be quoted with single or double quotes; quotes are stripped.
    - Inline comments (``KEY=value  # comment``) are NOT supported to avoid
      ambiguity with values that legitimately contain ``#``. Keep comments on
      their own lines.
    """
    env: dict[str, str] = {}
    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            m = _QUOTE_RE.match(value)
            if m:
                value = m.group(2)
            env[key] = value
    return env


def load_env_file(path: Path | str | None = None) -> None:
    """Load a .env file into ``os.environ`` without overwriting existing vars."""
    if path is None:
        path = Path(".env")
    path = Path(path)
    if not path.exists():
        return
    for key, value in _parse_env_file(path).items():
        os.environ.setdefault(key, value)


# ── helpers ───────────────────────────────────────────────────────────────────

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_bool(key: str, default: bool = True) -> bool:
    val = _env(key, str(default)).lower()
    return val not in {"0", "false", "no", "off"}


def _env_int(key: str, default: int = 0) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


# ── dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class Config:
    """All runtime settings for truenas-config-backup."""

    # TrueNAS connection
    host: str = ""
    api_key: str = ""
    verify_ssl: bool = True
    legacy_ws: bool = False

    # Download behaviour
    buffered_download: bool = True
    # Bundle options: control what config.save includes. TrueNAS only produces a
    # tar archive when at least one of these is true; with neither, it returns a
    # bare .db file (see validate()).
    secret_seed: bool = True
    root_authorized_keys: bool = False

    # Storage
    backup_dir: Path = field(default_factory=lambda: Path("backups"))

    # Retention
    retain_daily: int = 7
    retain_weekly: int = 4
    retain_monthly: int = 2

    # Notifications
    notify_on: str = "failure"   # "always" | "failure" | "never"
    notify_email: str = ""
    smtp_host: str = "localhost"
    smtp_port: int = 25
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "truenas-backup@localhost"

    # Logging
    log_level: str = "INFO"
    log_file: Path | None = None

    def __post_init__(self) -> None:
        if self.log_file is None:
            self.log_file = self.backup_dir / "backup.log"

    def validate(self) -> None:
        """Raise ValueError for obviously wrong settings."""
        errors: list[str] = []
        if not self.host:
            errors.append("TRUENAS_HOST is required")
        if not self.api_key:
            errors.append("TRUENAS_API_KEY is required")
        if self.retain_daily < 1:
            errors.append("RETAIN_DAILY must be >= 1")
        if self.retain_weekly < 1:
            errors.append("RETAIN_WEEKLY must be >= 1")
        if self.retain_monthly < 1:
            errors.append("RETAIN_MONTHLY must be >= 1")
        if self.notify_on not in {"always", "failure", "never"}:
            errors.append("NOTIFY_ON must be one of: always, failure, never")
        # TrueNAS only builds a tar bundle when at least one bundle option is
        # set; with neither, config.save returns a bare .db that would be saved
        # under a .tar name. Reject that combination rather than mislabel output.
        if not self.secret_seed and not self.root_authorized_keys:
            errors.append(
                "At least one of TRUENAS_SECRET_SEED or "
                "TRUENAS_ROOT_AUTHORIZED_KEYS must be true, otherwise TrueNAS "
                "returns a bare .db file instead of a tar archive."
            )
        # A notify policy that needs email but has no recipient silently never
        # notifies — surface it as a config error.
        if self.notify_on != "never" and not self.notify_email:
            errors.append(
                "NOTIFY_EMAIL is required when NOTIFY_ON is not 'never'."
            )
        if errors:
            raise ValueError("Configuration errors:\n  " + "\n  ".join(errors))


def build_config(env_file: Path | str | None = None) -> Config:
    """Build a :class:`Config` from environment variables (+ optional .env file)."""
    load_env_file(env_file)

    backup_dir = Path(_env("BACKUP_DIR", "backups"))
    log_file_raw = _env("LOG_FILE", "")

    return Config(
        host=_env("TRUENAS_HOST"),
        api_key=_env("TRUENAS_API_KEY"),
        verify_ssl=_env_bool("TRUENAS_VERIFY_SSL", True),
        legacy_ws=_env_bool("TRUENAS_LEGACY_WS", False),
        buffered_download=_env_bool("TRUENAS_BUFFERED_DOWNLOAD", True),
        secret_seed=_env_bool("TRUENAS_SECRET_SEED", True),
        root_authorized_keys=_env_bool("TRUENAS_ROOT_AUTHORIZED_KEYS", False),
        backup_dir=backup_dir,
        retain_daily=_env_int("RETAIN_DAILY", 7),
        retain_weekly=_env_int("RETAIN_WEEKLY", 4),
        retain_monthly=_env_int("RETAIN_MONTHLY", 2),
        notify_on=_env("NOTIFY_ON", "failure"),
        notify_email=_env("NOTIFY_EMAIL"),
        smtp_host=_env("SMTP_HOST", "localhost"),
        smtp_port=_env_int("SMTP_PORT", 25),
        smtp_user=_env("SMTP_USER"),
        smtp_password=_env("SMTP_PASSWORD"),
        smtp_from=_env("SMTP_FROM", "truenas-backup@localhost"),
        log_level=_env("LOG_LEVEL", "INFO").upper(),
        log_file=Path(log_file_raw) if log_file_raw else None,
    )
