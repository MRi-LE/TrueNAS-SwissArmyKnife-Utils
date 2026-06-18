"""
truenas_backup.client
~~~~~~~~~~~~~~~~~~~~~
Thin wrapper around the TrueNAS WebSocket JSON-RPC API.

Auth & config-download flow (confirmed from TrueNAS docs and forums):

    with Client(uri=ws_url, verify_ssl=verify_ssl) as c:
        c.call("auth.login_with_api_key", api_key)
        info = c.call("system.info")
        job_id, download_url = c.call(
            "core.download", "config.save", [opts], filename, buffered=True
        )
        # download_url is a path like /_download/46061?auth_token=xxxx
        # Auth token is embedded in the URL — no extra header needed.
        # Prepend https://{host} and stream with requests.

WebSocket endpoint:
  - TrueNAS >= 25.x (SCALE):  wss://{host}/api/current
  - TrueNAS <= 24.10 (CORE):  wss://{host}/websocket   (TRUENAS_LEGACY_WS=true)

verify_ssl support in truenas_api_client:
  - Available from the 25.10 client line onward.
  - 25.04 client will raise TypeError if verify_ssl= is passed to Client().
  - This module detects support via inspect.signature at call time and either
    omits the argument (verify_ssl=True config) or raises a clear error
    (verify_ssl=False config) when the installed client is too old.

For local on-NAS execution as root (not targeted by this project):
  ws+unix:///var/run/middleware/middlewared.sock — no auth needed.

Rate-limit note: TrueNAS enforces <= 20 auth attempts per 60 s.
One persistent connection per backup run is the correct pattern.

Third-party deps used here only:
  - truenas_api_client  (installed from GitHub tag matching server version)
  - requests            (streaming HTTP download of the config archive)
"""

from __future__ import annotations

import inspect
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import requests

log = logging.getLogger(__name__)

_CHUNK = 256 * 1024  # 256 KB streaming chunk size

_INSTALL_HINT = (
    "truenas_api_client is not installed.\n"
    "Install it with:\n"
    "  pip install git+https://github.com/truenas/api_client.git@<your-server-version>\n"
    "See README for version matching guidance."
)

_VERIFY_SSL_HINT = (
    "Your installed truenas_api_client does not support the verify_ssl argument.\n"
    "This argument was added in the 25.10 client line.\n"
    "Either:\n"
    "  - Install a newer client:  "
    "pip install git+https://github.com/truenas/api_client.git@25.10.0\n"
    "  - Or keep TRUENAS_VERIFY_SSL=true (safe on trusted networks with valid certs)."
)


@dataclass
class SystemInfo:
    hostname: str
    version: str


def _client_supports_verify_ssl() -> bool:
    """Return True if the installed Client.__init__ accepts a verify_ssl parameter.

    Checked against the real imported class at call time, not against any mock.
    Uses inspect.signature so no Client instance is created for the probe.
    """
    try:
        from truenas_api_client import Client  # noqa: PLC0415
    except ImportError:
        return False  # will be caught properly in _import_client()
    return "verify_ssl" in inspect.signature(Client.__init__).parameters


def _import_client():
    """Import and return truenas_api_client.Client with a clear error if missing."""
    try:
        from truenas_api_client import Client  # noqa: PLC0415
        return Client
    except ImportError as exc:
        raise RuntimeError(_INSTALL_HINT) from exc


def _build_client(Client, uri: str, verify_ssl: bool):  # noqa: N803
    """Construct a Client instance with verify_ssl compatibility handling.

    - If the installed client supports verify_ssl: pass it unconditionally.
    - If unsupported and verify_ssl=True: omit the argument (safe — the default
      behaviour without the arg is to verify).
    - If unsupported and verify_ssl=False: raise a clear actionable error.
    """
    if "verify_ssl" in inspect.signature(Client.__init__).parameters:
        return Client(uri=uri, verify_ssl=verify_ssl)
    # Older client — verify_ssl not supported
    if not verify_ssl:
        raise RuntimeError(_VERIFY_SSL_HINT)
    # verify_ssl=True: omit the arg, proceed with default SSL verification
    log.debug(
        "Installed truenas_api_client does not support verify_ssl=; "
        "proceeding with default SSL verification."
    )
    return Client(uri=uri)


class TrueNASBackupClient:
    """
    WebSocket JSON-RPC client for TrueNAS config backup.

    Args:
        host:              Hostname or IP of the TrueNAS server.
        api_key:           TrueNAS API key.
        verify_ssl:        Verify TLS certificate (default: True).
        legacy_ws:         Use /websocket endpoint for TrueNAS <= 24.10.
        buffered_download: Pass buffered=True to core.download (default: True).
                           Buffered mode keeps the download URL valid longer;
                           False streams immediately but the job blocks for up
                           to 60 s if the client is slow to connect.
    """

    def __init__(
        self,
        host: str,
        api_key: str,
        verify_ssl: bool = True,
        legacy_ws: bool | None = None,
        buffered_download: bool = True,
    ) -> None:
        host = host.rstrip("/")
        if "://" in host:
            host = host.split("://", 1)[1]

        self._host = host
        self._api_key = api_key
        self._verify_ssl = verify_ssl
        self._buffered_download = buffered_download

        if legacy_ws is None:
            legacy_ws = os.environ.get("TRUENAS_LEGACY_WS", "").lower() in {
                "1", "true", "yes", "on"
            }
        ws_path = "/websocket" if legacy_ws else "/api/current"
        self._ws_uri = f"wss://{host}{ws_path}"
        self._http_base = f"https://{host}"

    # ── public API ────────────────────────────────────────────────────────────

    def get_system_info(self) -> SystemInfo:
        """Connect, authenticate, and return the NAS hostname + version."""
        Client = _import_client()
        log.debug("Connecting to %s for system.info", self._ws_uri)
        with _build_client(Client, self._ws_uri, self._verify_ssl) as c:
            c.call("auth.login_with_api_key", self._api_key)
            data = c.call("system.info")
        hostname = data.get("hostname", "truenas")
        version = data.get("version", "unknown")
        log.debug("system.info: hostname=%s version=%s", hostname, version)
        return SystemInfo(hostname=hostname, version=version)

    def download_config(
        self,
        dest: Path,
        secret_seed: bool = True,
        root_authorized_keys: bool = False,
    ) -> int:
        """
        Download the TrueNAS system configuration archive to *dest*.

        Steps:
          1. Import and probe truenas_api_client.Client for verify_ssl support.
          2. Open one WebSocket connection, authenticate, fetch system.info.
          3. Call core.download("config.save", [opts], filename, buffered=...).
          4. HTTP-GET the download URL (auth token is embedded in the URL).
          5. Stream to dest.tmp then rename atomically.

        Returns:
            Number of bytes written.
        """
        Client = _import_client()

        opts = {
            "secretseed": secret_seed,
            "root_authorized_keys": root_authorized_keys,
            # pool_keys is deprecated/ignored on SCALE; kept for CORE compat
            "pool_keys": False,
        }

        log.debug("Connecting to %s for config download", self._ws_uri)
        with _build_client(Client, self._ws_uri, self._verify_ssl) as c:
            c.call("auth.login_with_api_key", self._api_key)

            info_data = c.call("system.info")
            hostname = info_data.get("hostname", "truenas")
            version = info_data.get("version", "unknown")
            log.info(
                "Connected to %s (TrueNAS %s) — requesting config archive",
                hostname, version,
            )

            # Build the target filename now that we have hostname + version so
            # it can be passed as the suggested filename to core.download.
            # The NAS uses it for Content-Disposition / server-side job logs.
            target_filename = dest.name

            job_id, download_url = c.call(
                "core.download",
                "config.save",
                [opts],
                target_filename,
                buffered=self._buffered_download,
            )
            # Never log download_url — it contains an auth_token= query param.
            log.debug(
                "core.download job_id=%s — download URL received", job_id
            )

        full_url = f"{self._http_base}{download_url}"
        log.debug("Fetching config archive for job %s", job_id)

        try:
            with requests.get(
                full_url,
                stream=True,
                verify=self._verify_ssl,
                timeout=120,
            ) as resp:
                resp.raise_for_status()
                return _stream_to_file(resp, dest)
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Failed to download config archive from {self._http_base}: {exc}"
            ) from exc


# ── helpers ───────────────────────────────────────────────────────────────────

def _stream_to_file(response: requests.Response, dest: Path) -> int:
    """Stream a requests response body to *dest* atomically; return bytes written.

    The downloaded archive contains the password secret seed (pwenc_secret), so
    it is written with owner-only (0600) permissions and never left briefly
    world-readable: the temp file is created 0600 before any bytes are written.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / (dest.name + ".tmp")
    bytes_written = 0
    try:
        # Create with mode 0600 so the secret material is never group/world-
        # readable, even momentarily, regardless of process umask. O_TRUNC lets
        # us overwrite a stale .tmp left by a previously interrupted run.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            for chunk in response.iter_content(chunk_size=_CHUNK):
                if chunk:
                    fh.write(chunk)
                    bytes_written += len(chunk)
        tmp.rename(dest)
        # Re-assert 0600 on the final path (rename preserves mode, but be explicit).
        dest.chmod(0o600)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    log.debug("Wrote %d bytes -> %s", bytes_written, dest)
    return bytes_written
