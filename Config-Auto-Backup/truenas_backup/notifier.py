"""
truenas_backup.notifier
~~~~~~~~~~~~~~~~~~~~~~~
Optional e-mail notification on backup success or failure.

Uses stdlib smtplib only — no external dependencies.
"""

from __future__ import annotations

import logging
import smtplib
import socket
from email.message import EmailMessage

from .config import Config

log = logging.getLogger(__name__)


class Notifier:
    """Sends SMTP e-mail notifications according to the ``notify_on`` policy."""

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg

    def notify(self, success: bool, detail: str = "") -> None:
        """Send a notification if the policy warrants it.

        Args:
            success: True if the backup completed without error.
            detail:  Short human-readable summary or error message.
        """
        policy = self._cfg.notify_on
        recipient = self._cfg.notify_email

        if policy == "never" or not recipient:
            return
        if policy == "failure" and success:
            return

        subject = (
            "[truenas-backup] SUCCESS" if success
            else "[truenas-backup] FAILURE"
        )
        body = self._build_body(success, detail)

        try:
            self._send(recipient, subject, body)
            log.info("Notification sent to %s (%s)", recipient, "success" if success else "failure")
        except Exception as exc:
            # Notification failure must never mask the backup outcome
            log.warning("Failed to send notification: %s", exc)

    # ── internal ────────────────────────────────────────────────────────────

    def _build_body(self, success: bool, detail: str) -> str:
        host = self._cfg.host
        status = "completed successfully" if success else "FAILED"
        lines = [
            f"TrueNAS config backup {status}.",
            f"  Host:   {host}",
        ]
        if detail:
            lines.append(f"  Detail: {detail}")
        return "\n".join(lines)

    def _send(self, recipient: str, subject: str, body: str) -> None:
        cfg = self._cfg
        msg = EmailMessage()
        msg["From"] = cfg.smtp_from
        msg["To"] = recipient
        msg["Subject"] = subject
        msg.set_content(body)

        if cfg.smtp_port == 465:
            smtp_cls = smtplib.SMTP_SSL
        else:
            smtp_cls = smtplib.SMTP

        with smtp_cls(cfg.smtp_host, cfg.smtp_port, timeout=15) as smtp:
            if cfg.smtp_port == 587:
                smtp.starttls()
            if cfg.smtp_user and cfg.smtp_password:
                smtp.login(cfg.smtp_user, cfg.smtp_password)
            smtp.send_message(msg)
