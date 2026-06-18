"""Tests for truenas_backup.notifier."""

from unittest.mock import MagicMock, patch

import pytest

from truenas_backup.config import Config
from truenas_backup.notifier import Notifier


def _cfg(**kwargs) -> Config:
    defaults = dict(host="h", api_key="k", notify_email="admin@example.com")
    defaults.update(kwargs)
    cfg = Config(**defaults)
    cfg.__post_init__()
    return cfg


def test_notify_never_does_not_send():
    cfg = _cfg(notify_on="never")
    notifier = Notifier(cfg)
    with patch("smtplib.SMTP") as smtp_cls:
        notifier.notify(success=False, detail="error")
    smtp_cls.assert_not_called()


def test_notify_failure_only_sends_on_failure():
    cfg = _cfg(notify_on="failure")
    notifier = Notifier(cfg)
    with patch("smtplib.SMTP") as smtp_cls:
        smtp_instance = smtp_cls.return_value.__enter__.return_value
        notifier.notify(success=False, detail="oops")
        smtp_instance.send_message.assert_called_once()

    with patch("smtplib.SMTP") as smtp_cls:
        notifier.notify(success=True, detail="all good")
        smtp_cls.assert_not_called()


def test_notify_always_sends_on_success():
    cfg = _cfg(notify_on="always")
    notifier = Notifier(cfg)
    with patch("smtplib.SMTP") as smtp_cls:
        smtp_instance = smtp_cls.return_value.__enter__.return_value
        notifier.notify(success=True, detail="/backups/daily/foo.tar")
        smtp_instance.send_message.assert_called_once()


def test_notify_no_recipient_skips():
    cfg = _cfg(notify_on="always", notify_email="")
    notifier = Notifier(cfg)
    with patch("smtplib.SMTP") as smtp_cls:
        notifier.notify(success=True)
    smtp_cls.assert_not_called()


def test_smtp_error_does_not_raise():
    """A broken SMTP server must never mask a backup success."""
    cfg = _cfg(notify_on="always")
    notifier = Notifier(cfg)
    with patch("smtplib.SMTP", side_effect=ConnectionRefusedError("refused")):
        notifier.notify(success=True, detail="ok")  # must not raise
