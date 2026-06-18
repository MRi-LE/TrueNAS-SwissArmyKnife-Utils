"""Tests for truenas_backup.client."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from truenas_backup.client import (
    TrueNASBackupClient,
    SystemInfo,
    _stream_to_file,
    _build_client,
    _client_supports_verify_ssl,
)


# ---------------------------------------------------------------------------
# Fake Client classes for verify_ssl compatibility tests
# Plain classes with explicit __init__ signatures — no **kwargs, no MagicMock.
# inspect.signature() must see exactly what is declared.
# ---------------------------------------------------------------------------

class FakeClientSupportsVerifySsl:
    """Simulates a truenas_api_client.Client that accepts verify_ssl."""

    def __init__(self, uri, verify_ssl=True):
        self.uri = uri
        self.verify_ssl = verify_ssl
        self._calls = []

    def call(self, method, *args, **kwargs):
        self._calls.append((method, args, kwargs))
        if method == "auth.login_with_api_key":
            return True
        if method == "system.info":
            return {"hostname": "mybox", "version": "25.10"}
        if method == "core.download":
            return (1, "/_download/1?auth_token=tok")
        raise ValueError(f"Unexpected: {method}")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeClientNoVerifySsl:
    """Simulates a truenas_api_client.Client that does NOT accept verify_ssl."""

    def __init__(self, uri):
        self.uri = uri
        self._calls = []

    def call(self, method, *args, **kwargs):
        self._calls.append((method, args, kwargs))
        if method == "auth.login_with_api_key":
            return True
        if method == "system.info":
            return {"hostname": "mybox", "version": "25.04"}
        if method == "core.download":
            return (1, "/_download/1?auth_token=tok")
        raise ValueError(f"Unexpected: {method}")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_client(**kwargs) -> TrueNASBackupClient:
    defaults = dict(host="truenas.local", api_key="test-key", verify_ssl=False)
    defaults.update(kwargs)
    return TrueNASBackupClient(**defaults)


def _requests_get_mock(content: bytes = b"fake-tar-content") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.iter_content = MagicMock(return_value=iter([content]))
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# verify_ssl compatibility — _build_client()
# ---------------------------------------------------------------------------

def test_build_client_passes_verify_ssl_when_supported():
    """When Client accepts verify_ssl, it must be passed — even when False."""
    instance = _build_client(FakeClientSupportsVerifySsl, "wss://host/api/current", False)
    assert instance.verify_ssl is False


def test_build_client_omits_verify_ssl_when_unsupported_and_ssl_true():
    """Older client + verify_ssl=True: construct without the arg, no error."""
    instance = _build_client(FakeClientNoVerifySsl, "wss://host/api/current", True)
    assert instance.uri == "wss://host/api/current"


def test_build_client_raises_when_unsupported_and_ssl_false():
    """Older client + verify_ssl=False: raise a clear, actionable error."""
    with pytest.raises(RuntimeError, match="verify_ssl"):
        _build_client(FakeClientNoVerifySsl, "wss://host/api/current", False)


def test_build_client_error_mentions_upgrade_path():
    """The error message must tell the user exactly how to fix the problem."""
    with pytest.raises(RuntimeError, match="25.10"):
        _build_client(FakeClientNoVerifySsl, "wss://host/api/current", False)


# ---------------------------------------------------------------------------
# Missing truenas_api_client — clear install hint
# ---------------------------------------------------------------------------

def test_import_error_gives_install_hint(monkeypatch):
    """If truenas_api_client is absent, the error must include the install command."""
    # Temporarily remove the module from sys.modules so the import fails
    original = sys.modules.pop("truenas_api_client", None)
    try:
        # Also make the import itself fail
        import builtins
        real_import = builtins.__import__

        def failing_import(name, *args, **kwargs):
            if name == "truenas_api_client":
                raise ImportError("No module named 'truenas_api_client'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", failing_import)

        from truenas_backup.client import _import_client
        with pytest.raises(RuntimeError, match="pip install"):
            _import_client()
    finally:
        if original is not None:
            sys.modules["truenas_api_client"] = original


# ---------------------------------------------------------------------------
# URI construction
# ---------------------------------------------------------------------------

def test_default_endpoint_is_api_current(monkeypatch):
    monkeypatch.delenv("TRUENAS_LEGACY_WS", raising=False)
    c = _make_client(verify_ssl=True)
    assert c._ws_uri == "wss://truenas.local/api/current"
    assert c._http_base == "https://truenas.local"


def test_legacy_ws_kwarg():
    c = _make_client(legacy_ws=True, verify_ssl=True)
    assert c._ws_uri == "wss://truenas.local/websocket"


def test_legacy_ws_env(monkeypatch):
    monkeypatch.setenv("TRUENAS_LEGACY_WS", "true")
    c = _make_client(verify_ssl=True)
    assert c._ws_uri == "wss://truenas.local/websocket"


def test_host_scheme_stripped():
    c = _make_client(host="https://truenas.local/", verify_ssl=True)
    assert c._ws_uri == "wss://truenas.local/api/current"


# ---------------------------------------------------------------------------
# download_config — functional tests using FakeClientSupportsVerifySsl
# ---------------------------------------------------------------------------

def _patched_download(tmp_path, client_cls=None, content=b"fake-tar", **client_kwargs):
    """Helper: run download_config() with patched Client and requests."""
    if client_cls is None:
        client_cls = FakeClientSupportsVerifySsl
    dest = tmp_path / "truenas-mybox-25.10-20250511-020000.tar"
    resp_mock = _requests_get_mock(content=content)
    c = _make_client(**client_kwargs)
    with (
        patch("truenas_backup.client._import_client", return_value=client_cls),
        patch("truenas_backup.client.requests.get", return_value=resp_mock) as get_mock,
    ):
        size = c.download_config(dest)
    return dest, size, get_mock


def test_download_config_writes_archive(tmp_path):
    content = b"PK fake tar archive bytes"
    dest, size, _ = _patched_download(tmp_path, content=content, verify_ssl=True)
    assert dest.exists()
    assert dest.read_bytes() == content
    assert size == len(content)


def test_download_config_no_extra_auth_header(tmp_path):
    """Auth token is in the URL — no Authorization header must be injected."""
    _, _, get_mock = _patched_download(tmp_path, verify_ssl=True)
    _, kwargs = get_mock.call_args
    assert "headers" not in kwargs


def test_download_config_url_is_http_base_plus_path(tmp_path):
    dest = tmp_path / "truenas-mybox-25.10-20250511-020000.tar"
    resp_mock = _requests_get_mock()

    # Use a fake client that returns a known download path
    class _FakeWithKnownUrl(FakeClientSupportsVerifySsl):
        def call(self, method, *args, **kwargs):
            if method == "core.download":
                return (7, "/_download/7?auth_token=aaa")
            return super().call(method, *args, **kwargs)

    c = TrueNASBackupClient(host="mynas.example.com", api_key="k", verify_ssl=True)
    with (
        patch("truenas_backup.client._import_client", return_value=_FakeWithKnownUrl),
        patch("truenas_backup.client.requests.get", return_value=resp_mock) as get_mock,
    ):
        c.download_config(dest)

    url_called = get_mock.call_args[0][0]
    assert url_called == "https://mynas.example.com/_download/7?auth_token=aaa"


def test_download_config_single_ws_connection(tmp_path):
    """One Client instantiation covers auth + system.info + core.download."""
    dest = tmp_path / "truenas-mybox-25.10-20250511-020000.tar"
    resp_mock = _requests_get_mock()
    instantiation_count = []

    class _CountingClient(FakeClientSupportsVerifySsl):
        def __init__(self, *args, **kwargs):
            instantiation_count.append(1)
            super().__init__(*args, **kwargs)

    c = _make_client(verify_ssl=True)
    with (
        patch("truenas_backup.client._import_client", return_value=_CountingClient),
        patch("truenas_backup.client.requests.get", return_value=resp_mock),
    ):
        c.download_config(dest)

    assert len(instantiation_count) == 1


def test_download_config_cleans_tmp_on_request_error(tmp_path):
    import requests as req_lib
    dest = tmp_path / "truenas-mybox-25.10-20250511-020000.tar"

    c = _make_client(verify_ssl=True)
    with (
        patch("truenas_backup.client._import_client", return_value=FakeClientSupportsVerifySsl),
        patch(
            "truenas_backup.client.requests.get",
            side_effect=req_lib.RequestException("refused"),
        ),
    ):
        with pytest.raises(RuntimeError, match="Failed to download"):
            c.download_config(dest)

    assert not dest.exists()
    assert not dest.with_suffix(".tmp").exists()


def test_download_config_passes_secret_seed_false(tmp_path):
    dest = tmp_path / "truenas-mybox-25.10-20250511-020000.tar"
    resp_mock = _requests_get_mock()
    captured = []

    class _CapturingClient(FakeClientSupportsVerifySsl):
        def call(self, method, *args, **kwargs):
            captured.append((method, args, kwargs))
            return super().call(method, *args, **kwargs)

    c = _make_client(verify_ssl=True)
    with (
        patch("truenas_backup.client._import_client", return_value=_CapturingClient),
        patch("truenas_backup.client.requests.get", return_value=resp_mock),
    ):
        c.download_config(dest, secret_seed=False)

    core_dl = next(c for c in captured if c[0] == "core.download")
    opts = core_dl[1][1][0]   # core.download, positional args, [opts] list, first element
    assert opts["secretseed"] is False


def test_download_config_passes_buffered_flag(tmp_path):
    """buffered= must be passed as a keyword arg to core.download."""
    dest = tmp_path / "truenas-mybox-25.10-20250511-020000.tar"
    resp_mock = _requests_get_mock()
    captured = []

    class _CapturingClient(FakeClientSupportsVerifySsl):
        def call(self, method, *args, **kwargs):
            captured.append((method, args, kwargs))
            return super().call(method, *args, **kwargs)

    c = TrueNASBackupClient(
        host="truenas.local", api_key="k", verify_ssl=True, buffered_download=True
    )
    with (
        patch("truenas_backup.client._import_client", return_value=_CapturingClient),
        patch("truenas_backup.client.requests.get", return_value=resp_mock),
    ):
        c.download_config(dest)

    core_dl = next(c for c in captured if c[0] == "core.download")
    assert core_dl[2].get("buffered") is True


def test_download_config_passes_real_filename(tmp_path):
    """The destination filename must be passed to core.download, not 'backup'."""
    dest = tmp_path / "truenas-mybox-25.10-20250511-020000.tar"
    resp_mock = _requests_get_mock()
    captured = []

    class _CapturingClient(FakeClientSupportsVerifySsl):
        def call(self, method, *args, **kwargs):
            captured.append((method, args, kwargs))
            return super().call(method, *args, **kwargs)

    c = _make_client(verify_ssl=True)
    with (
        patch("truenas_backup.client._import_client", return_value=_CapturingClient),
        patch("truenas_backup.client.requests.get", return_value=resp_mock),
    ):
        c.download_config(dest)

    core_dl = next(c for c in captured if c[0] == "core.download")
    filename_arg = core_dl[1][2]  # third positional arg to core.download
    assert filename_arg == dest.name
    assert filename_arg != "backup"


def test_download_config_does_not_log_auth_token(tmp_path, caplog):
    """Auth token in download URL must never appear in any log output."""
    import logging
    dest = tmp_path / "truenas-mybox-25.10-20250511-020000.tar"

    class _TokenClient(FakeClientSupportsVerifySsl):
        def call(self, method, *args, **kwargs):
            if method == "core.download":
                return (99, "/_download/99?auth_token=SUPERSECRET")
            return super().call(method, *args, **kwargs)

    resp_mock = _requests_get_mock()
    c = _make_client(verify_ssl=True)
    with (
        patch("truenas_backup.client._import_client", return_value=_TokenClient),
        patch("truenas_backup.client.requests.get", return_value=resp_mock),
        caplog.at_level(logging.DEBUG, logger="truenas_backup.client"),
    ):
        c.download_config(dest)

    full_log = caplog.text
    assert "SUPERSECRET" not in full_log
    assert "auth_token" not in full_log


# ---------------------------------------------------------------------------
# _stream_to_file
# ---------------------------------------------------------------------------

def test_stream_to_file_atomic(tmp_path):
    dest = tmp_path / "out.tar"
    resp = MagicMock()
    resp.iter_content = MagicMock(return_value=iter([b"chunk1", b"chunk2"]))

    size = _stream_to_file(resp, dest)

    assert dest.exists()
    assert dest.read_bytes() == b"chunk1chunk2"
    assert size == 12
    assert not dest.with_suffix(".tmp").exists()


def test_stream_to_file_removes_tmp_on_error(tmp_path):
    dest = tmp_path / "out.tar"
    resp = MagicMock()
    resp.iter_content = MagicMock(side_effect=OSError("disk full"))

    with pytest.raises(OSError):
        _stream_to_file(resp, dest)

    assert not dest.with_suffix(".tmp").exists()
    assert not dest.exists()
