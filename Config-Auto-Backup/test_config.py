"""Tests for truenas_backup.config."""

import os
from pathlib import Path

import pytest

from truenas_backup.config import _parse_env_file, build_config, Config


# ---------------------------------------------------------------------------
# _parse_env_file — basic parsing
# ---------------------------------------------------------------------------

def test_parse_basic_key_value(tmp_path):
    env = tmp_path / ".env"
    env.write_text("FOO=bar\nBAZ=qux\n")
    assert _parse_env_file(env) == {"FOO": "bar", "BAZ": "qux"}


def test_parse_comments_and_blank_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\n\nKEY=value\n")
    assert _parse_env_file(env) == {"KEY": "value"}


def test_parse_double_quoted_value(tmp_path):
    env = tmp_path / ".env"
    env.write_text('TRUENAS_API_KEY="1-abc123"\n')
    assert _parse_env_file(env) == {"TRUENAS_API_KEY": "1-abc123"}


def test_parse_single_quoted_value(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TRUENAS_HOST='truenas.local'\n")
    assert _parse_env_file(env) == {"TRUENAS_HOST": "truenas.local"}


def test_parse_quoted_value_with_spaces(tmp_path):
    env = tmp_path / ".env"
    env.write_text('SMTP_FROM="backup bot@example.com"\n')
    assert _parse_env_file(env) == {"SMTP_FROM": "backup bot@example.com"}


def test_parse_inline_comment_is_not_stripped(tmp_path):
    """Inline comments are intentionally NOT supported — the # becomes part of the value.

    This is the parser trap: users must not write:
        KEY=value  # comment
    because the comment text becomes part of the parsed value.
    This test documents and locks that behaviour so .env.example is kept clean.
    """
    env = tmp_path / ".env"
    env.write_text("TRUENAS_HOST=truenas.local  # my NAS\n")
    result = _parse_env_file(env)
    # The comment is included in the value — this is expected parser behaviour.
    assert result["TRUENAS_HOST"] == "truenas.local  # my NAS"


def test_parse_mismatched_quotes_are_not_stripped(tmp_path):
    """Only matching quote pairs are stripped."""
    env = tmp_path / ".env"
    env.write_text("KEY='value\"\n")
    assert _parse_env_file(env) == {"KEY": "'value\""}


# ---------------------------------------------------------------------------
# Config.validate
# ---------------------------------------------------------------------------

def test_validate_raises_without_host():
    cfg = Config(host="", api_key="key123")
    with pytest.raises(ValueError, match="TRUENAS_HOST"):
        cfg.validate()


def test_validate_raises_without_key():
    cfg = Config(host="truenas.local", api_key="")
    with pytest.raises(ValueError, match="TRUENAS_API_KEY"):
        cfg.validate()


def test_validate_passes_with_valid_config():
    cfg = Config(host="truenas.local", api_key="key123", notify_on="never")
    cfg.validate()  # must not raise


def test_validate_passes_with_notify_email():
    cfg = Config(
        host="truenas.local",
        api_key="key123",
        notify_on="failure",
        notify_email="admin@example.com",
    )
    cfg.validate()  # must not raise


def test_validate_requires_email_when_notifying():
    cfg = Config(host="h", api_key="k", notify_on="failure", notify_email="")
    with pytest.raises(ValueError, match="NOTIFY_EMAIL"):
        cfg.validate()


def test_validate_rejects_no_bundle_options():
    cfg = Config(
        host="h",
        api_key="k",
        notify_on="never",
        secret_seed=False,
        root_authorized_keys=False,
    )
    with pytest.raises(ValueError, match="bare .db"):
        cfg.validate()


def test_validate_allows_root_keys_without_seed():
    cfg = Config(
        host="h",
        api_key="k",
        notify_on="never",
        secret_seed=False,
        root_authorized_keys=True,
    )
    cfg.validate()  # must not raise


def test_validate_bad_notify_on():
    cfg = Config(host="h", api_key="k", notify_on="maybe")
    with pytest.raises(ValueError, match="NOTIFY_ON"):
        cfg.validate()


# ---------------------------------------------------------------------------
# build_config — from env file
# ---------------------------------------------------------------------------

def test_build_config_from_env_file(tmp_path, monkeypatch):
    for key in ["TRUENAS_HOST", "TRUENAS_API_KEY", "RETAIN_DAILY"]:
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text("TRUENAS_HOST=mynas\nTRUENAS_API_KEY=abc\nRETAIN_DAILY=14\n")
    cfg = build_config(env_file=env)
    assert cfg.host == "mynas"
    assert cfg.api_key == "abc"
    assert cfg.retain_daily == 14


def test_build_config_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUENAS_HOST", "h")
    monkeypatch.setenv("TRUENAS_API_KEY", "k")
    for key in ["RETAIN_DAILY", "RETAIN_WEEKLY", "RETAIN_MONTHLY",
                "TRUENAS_BUFFERED_DOWNLOAD", "TRUENAS_LEGACY_WS"]:
        monkeypatch.delenv(key, raising=False)
    cfg = build_config(env_file=tmp_path / "nonexistent.env")
    assert cfg.retain_daily == 7
    assert cfg.retain_weekly == 4
    assert cfg.retain_monthly == 2
    assert cfg.buffered_download is True
    assert cfg.legacy_ws is False


def test_build_config_buffered_download_false(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUENAS_HOST", "h")
    monkeypatch.setenv("TRUENAS_API_KEY", "k")
    monkeypatch.setenv("TRUENAS_BUFFERED_DOWNLOAD", "false")
    cfg = build_config(env_file=tmp_path / "nonexistent.env")
    assert cfg.buffered_download is False


def test_build_config_buffered_download_true_explicit(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUENAS_HOST", "h")
    monkeypatch.setenv("TRUENAS_API_KEY", "k")
    monkeypatch.setenv("TRUENAS_BUFFERED_DOWNLOAD", "true")
    cfg = build_config(env_file=tmp_path / "nonexistent.env")
    assert cfg.buffered_download is True
