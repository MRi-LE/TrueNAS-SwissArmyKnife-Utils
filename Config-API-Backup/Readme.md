# truenas-config-backup

A production-quality Python tool for automated daily backups of TrueNAS SCALE / CORE system configuration via the **WebSocket JSON-RPC API**, with three-tier retention (daily / weekly / monthly).

Designed to run as a cron job or systemd timer on any host with network access to your NAS.

---

## Features

- **WebSocket JSON-RPC** — uses the official `truenas_api_client` library over a single persistent connection per run (respects TrueNAS's 20-auth/60s rate limit)
- **Three backup tiers** — daily, weekly (every Sunday), monthly (1st of the month)
- **Configurable retention** — keep N of each tier (defaults: 7 daily, 4 weekly, 2 monthly)
- **Timestamp-based pruning** — oldest files determined by the `YYYYMMDD-HHMMSS` embedded in the filename, not filesystem mtime (safe after `rsync`, copies, etc.)
- **Atomic writes** — stream to `.tmp` → `os.rename()`, so partial downloads never corrupt your archive
- **Dry-run mode** — preview what would happen without writing or deleting anything
- **Optional e-mail notifications** — on success, failure, or both
- **Rotating log file** — sits next to the backup directory
- **Legacy NAS support** — `TRUENAS_LEGACY_WS=true` switches to the `/websocket` endpoint for TrueNAS ≤ 24.10 (CORE)
- **`.env` / environment-variable config** — no secrets in scripts or cron entries

---

## Requirements

| Dependency | Version |
|---|---|
| Python | ≥ 3.9 |
| TrueNAS SCALE | ≥ 25.x (`/api/current` endpoint) |
| TrueNAS CORE / SCALE ≤ 24.10 | set `TRUENAS_LEGACY_WS=true` |

### Runtime dependencies

```bash
# requests — for streaming the HTTP config archive download
pip install requests

# truenas_api_client — NOT on PyPI; install from GitHub at the tag matching your server version
pip install git+https://github.com/truenas/api_client.git@25.10.0
# Replace 25.10.0 with your TrueNAS version tag (check: System → General → Version)
```

All other modules are Python stdlib.

> **`verify_ssl` compatibility:** Python-side SSL verification control (`verify_ssl=`) was added in the **25.10 client line**. If you install a 25.04 client tag, `TRUENAS_VERIFY_SSL=true` (the default) will still work — the argument is simply omitted and the client verifies by default. However, `TRUENAS_VERIFY_SSL=false` requires a 25.10+ client; the script will raise a clear error with upgrade instructions if the installed client is too old.

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/you/truenas-config-backup.git
cd truenas-config-backup

# 2. Install dependencies
pip install requests
pip install git+https://github.com/truenas/api_client.git@<your-truenas-version>

# 3. Copy and edit the example config
cp .env.example .env
chmod 600 .env
$EDITOR .env

# 4. Dry-run to verify connectivity
python run_backup.py --dry-run

# 5. Install as a daily cron job (runs at 02:00)
crontab -e
# add:  0 2 * * *  /usr/bin/python3 /opt/truenas-config-backup/run_backup.py
```

---

## Configuration

All settings are read from environment variables (or a `.env` file loaded at startup).

| Variable | Default | Description |
|---|---|---|
| `TRUENAS_HOST` | — | Hostname or IP of your TrueNAS box, e.g. `truenas.local` |
| `TRUENAS_API_KEY` | — | API key generated in TrueNAS UI → Settings → API Keys |
| `TRUENAS_VERIFY_SSL` | `true` | Set `false` to skip TLS verification (self-signed certs). Requires 25.10+ client — see note above. |
| `TRUENAS_LEGACY_WS` | `false` | Set `true` for TrueNAS ≤ 24.10 (uses `/websocket` endpoint) |
| `TRUENAS_BUFFERED_DOWNLOAD` | `true` | Buffered mode keeps the download URL valid longer. Set `false` only if you need immediate streaming (blocks up to 60 s if the client is slow). |
| `TRUENAS_SECRET_SEED` | `true` | Include the password secret seed (`pwenc_secret`). Required to restore passwords on different hardware. See note below. |
| `TRUENAS_ROOT_AUTHORIZED_KEYS` | `false` | Include `/root/.ssh/authorized_keys` in the archive. |
| `BACKUP_DIR` | `./backups` | Root directory for the three backup sub-folders |
| `RETAIN_DAILY` | `7` | Number of daily backups to keep |
| `RETAIN_WEEKLY` | `4` | Number of weekly backups to keep |
| `RETAIN_MONTHLY` | `2` | Number of monthly backups to keep |
| `NOTIFY_EMAIL` | _(empty)_ | Recipient address for status e-mails |
| `SMTP_HOST` | `localhost` | SMTP server for notifications |
| `SMTP_PORT` | `25` | SMTP port (587 → STARTTLS, 465 → SSL) |
| `SMTP_USER` | _(empty)_ | SMTP username (leave blank for unauthenticated) |
| `SMTP_PASSWORD` | _(empty)_ | SMTP password |
| `NOTIFY_ON` | `failure` | When to send e-mail: `always`, `failure`, `never` |
| `LOG_FILE` | `{BACKUP_DIR}/backup.log` | Path to rotating log file |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

> **At least one bundle option must be enabled.** TrueNAS only produces a tar
> archive when `TRUENAS_SECRET_SEED` and/or `TRUENAS_ROOT_AUTHORIZED_KEYS` is
> `true`. With neither, `config.save` returns a bare `.db` file that would be
> saved under a `.tar` name. `Config.validate()` rejects this combination, so a
> run with both set to `false` fails fast with a clear message rather than
> writing a mislabelled file.

> **Notification config is validated too.** If `NOTIFY_ON` is not `never` but
> `NOTIFY_EMAIL` is empty, validation fails — this prevents a config that
> silently never notifies.

> **Security — the archive contains secrets.** When `TRUENAS_SECRET_SEED` is
> enabled, the backup contains `pwenc_secret`, which can decrypt stored
> passwords. The downloaded file is written with owner-only (`0600`)
> permissions and is created `0600` from the first byte (regardless of process
> umask), so it is never momentarily group/world-readable. Store the backup
> directory on access-controlled storage and avoid syncing it to third-party
> cloud storage unencrypted.

### Generating a TrueNAS API Key

1. Log in to the TrueNAS web UI.
2. Go to **Settings → API Keys** (top-right menu on older versions).
3. Click **Add** → give it a name → **Generate Key**.
4. Copy the key into your `.env` file as `TRUENAS_API_KEY`.

> **Security tip:** Store `.env` with `chmod 600`. Never commit it to version control.

---

## Backup Layout

```
backups/
├── daily/
│   ├── truenas-mybox-25.04-20250511-020001.tar
│   ├── truenas-mybox-25.04-20250510-020001.tar
│   └── …  (up to RETAIN_DAILY files)
├── weekly/
│   ├── truenas-mybox-25.04-20250506-020001.tar   ← last Sunday
│   └── …  (up to RETAIN_WEEKLY files)
├── monthly/
│   ├── truenas-mybox-25.04-20250501-020001.tar   ← 1st of month
│   └── …  (up to RETAIN_MONTHLY files)
└── backup.log
```

### File naming convention

```
truenas-{hostname}-{version}-{YYYYMMDD}-{HHMMSS}.tar
```

The `.tar` is the native format TrueNAS produces. Each tier holds independent copies — pruning one tier never affects another.

---

## How It Works

```
run_backup.py
    │
    ├── build_config()              # reads .env / environment
    │
    ├── TrueNASBackupClient
    │   │   (one WebSocket connection per run)
    │   ├── auth.login_with_api_key # authenticate
    │   ├── system.info             # → hostname, version
    │   ├── core.download(          # → (job_id, download_url)
    │   │       "config.save",
    │   │       [{secretseed, …}],
    │   │       "backup"
    │   │   )
    │   └── requests.get(url)       # HTTP stream → .tmp → rename
    │
    ├── BackupManager
    │   ├── download → daily/
    │   ├── promote  → weekly/   (Sundays)
    │   ├── promote  → monthly/  (1st of month)
    │   └── prune each tier      (oldest-by-filename-timestamp first)
    │
    └── Notifier
        └── SMTP e-mail on success / failure
```

### WebSocket API calls

| Call | Args | Returns |
|---|---|---|
| `auth.login_with_api_key` | `api_key` | `True` |
| `system.info` | — | `{hostname, version, …}` |
| `core.download` | `"config.save"`, `[opts]`, `filename`, `buffered=True` | `(job_id, download_url)` |

The `download_url` (e.g. `/_download/46061?auth_token=xxxx`) is prepended with `https://{host}` and fetched via `requests.get(..., stream=True)`. The auth token is embedded in the URL — no extra header is needed.

### `config.save` options

| Option | Default | Notes |
|---|---|---|
| `secretseed` | `true` | Include secret seed; required to restore on different hardware |
| `root_authorized_keys` | `false` | Include `/root/.ssh/authorized_keys` |
| `pool_keys` | `false` | Deprecated on SCALE; kept for CORE compatibility |

---

## CLI Reference

```
usage: run_backup.py [-h] [--dry-run] [--config PATH] [--log-level LEVEL]

options:
  -h, --help          show this help message and exit
  --dry-run           Simulate backup and pruning without writing any files
  --config PATH       Path to .env file (default: .env in project root)
  --log-level LEVEL   Override LOG_LEVEL from config (DEBUG/INFO/WARNING/ERROR)
```

---

## Running as a Cron Job

```cron
# /etc/cron.d/truenas-backup  — run at 02:00 every day as dedicated user
0 2 * * *  truenas-backup  /usr/bin/python3 /opt/truenas-config-backup/run_backup.py
```

### systemd timer (alternative to cron)

Unit and timer files are provided in `contrib/systemd/`:

```bash
cp contrib/systemd/truenas-backup.service /etc/systemd/system/
cp contrib/systemd/truenas-backup.timer   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now truenas-backup.timer
```

---

## Testing

```bash
pip install pytest requests
pytest test_backup.py test_client.py test_config.py test_notifier.py -v
```

All tests mock `truenas_api_client.Client` and `requests` — no live NAS required. The fake `truenas_api_client` module is injected into `sys.modules` at test collection time so the lazy import inside client methods resolves correctly.

---

## Project Structure

```
truenas-config-backup/
├── truenas_backup/
│   ├── __init__.py                  # package marker
│   ├── backup.py                    # BackupManager — tiers, retention, atomic writes
│   ├── client.py                    # TrueNASBackupClient — WebSocket JSON-RPC + requests
│   ├── config.py                    # Config dataclass + .env loader (stdlib only)
│   ├── notifier.py                  # SMTP e-mail notifications (stdlib only)
│   └── utils.py                     # logging setup, path helpers (stdlib only)
├── contrib/
│   └── systemd/
│       ├── truenas-backup.service   # systemd oneshot service unit
│       └── truenas-backup.timer     # daily 02:00 timer with Persistent=true
├── run_backup.py                    # CLI entry point
├── test_backup.py                   # BackupManager tests
├── test_client.py                   # TrueNASBackupClient tests
├── test_config.py                   # config parsing and validation tests
├── test_notifier.py                 # SMTP notifier tests
├── .env.example                     # configuration template (copy to .env)
├── .gitignore                       # excludes .env, __pycache__, build artifacts
├── pyproject.toml                   # build config, deps, pytest/ruff settings
└── README.md
```

### `truenas_backup/` — installable package

**`__init__.py`**
Empty marker file that makes `truenas_backup/` a Python package. Required for the `from truenas_backup.client import ...` import style used throughout the project.

**`backup.py`** — `BackupManager`
Orchestrates the full backup lifecycle for a single run. Calls the client to download the config, copies it into the appropriate tier directories (daily always; weekly on Sundays; monthly on the 1st of the month), then prunes each tier to its configured retention limit. Retention sort uses the `YYYYMMDD-HHMMSS` timestamp embedded in the filename rather than filesystem mtime, so it stays correct after copies or `rsync`. Atomic writes (`.tmp` → `rename`) are handled here for inter-tier copies. Exposes `set_now()` for injecting a fixed datetime in tests.

**`client.py`** — `TrueNASBackupClient`
All network I/O lives here. Opens a single WebSocket JSON-RPC connection per backup run using `truenas_api_client.Client`, authenticates with the API key, calls `system.info` and `core.download`, then fetches the resulting archive over HTTPS with `requests`. Handles `truenas_api_client` version compatibility: detects whether the installed client supports `verify_ssl=` via `inspect.signature` at call time, raises a clear upgrade message if `TRUENAS_VERIFY_SSL=false` is configured but the client is too old, and raises an actionable `RuntimeError` with the install command if the library is missing entirely. Never logs the download URL or auth token.

**`config.py`** — `Config` + `.env` loader
Defines the `Config` dataclass that holds every runtime setting. Includes a minimal hand-rolled `.env` parser (stdlib only — no `python-dotenv`) that handles blank lines, `#`-comment lines, and single- or double-quoted values. Inline comments are explicitly unsupported to avoid ambiguity with values that legitimately contain `#`. `build_config()` reads all settings from environment variables after optionally loading a `.env` file, with typed helpers for booleans and integers. `Config.validate()` raises `ValueError` with a full list of problems rather than stopping at the first one.

**`notifier.py`** — `Notifier`
Sends plain-text status e-mails via SMTP after each backup run. Respects the `NOTIFY_ON` setting (`always`, `failure`, `never`). Uses `smtplib` and `email` from the stdlib only. A notification failure never masks or replaces the backup result — it is logged and swallowed so that a broken SMTP config does not hide a successful backup.

**`utils.py`** — shared helpers
Two small utilities used across the package: `setup_logging()` configures a rotating file handler and a console handler at the requested log level, and `ensure_dirs()` creates one or more directories (including parents) if they do not already exist. Both are stdlib-only.

### `contrib/systemd/` — systemd unit files

**`truenas-backup.service`**
A `Type=oneshot` systemd service unit that runs `run_backup.py` as a dedicated low-privilege user (`truenas-backup`). Includes basic hardening: `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, and a `ReadWritePaths` entry for the backup directory. Reads secrets from the `.env` file via `EnvironmentFile=`.

**`truenas-backup.timer`**
Triggers `truenas-backup.service` daily at 02:00. `Persistent=true` means the timer fires immediately on the next boot if a scheduled run was missed (e.g. after downtime), rather than waiting until the next 02:00 window.

### Root files

**`run_backup.py`** — CLI entry point
Parses command-line arguments (`--dry-run`, `--config`, `--log-level`), loads config, wires together `TrueNASBackupClient`, `BackupManager`, and `Notifier`, and exits with code `0` on success or `1` on failure. Intended for both direct invocation (`python run_backup.py`) and as the target of the cron/systemd entry.

**`test_backup.py`**
Tests for `BackupManager`: filename format, tier routing (daily-only on weekdays, weekly on Sundays, monthly on the 1st, both on a Sunday-1st), retention pruning ordered by filename timestamp rather than mtime, dry-run behaviour, and `ensure_dirs()` creating the full directory tree on first run.

**`test_client.py`**
Tests for `TrueNASBackupClient` and its helpers. Uses two plain fake `Client` classes with explicit `__init__` signatures (no `**kwargs`, no `MagicMock`) so `inspect.signature` returns deterministic results. Covers URI construction, the three `verify_ssl` compatibility branches, the missing-library install hint, archive download, single-connection behaviour, auth token absent from all log output, `buffered=` and real filename passed to `core.download`, and atomic temp-file cleanup on error.

**`test_config.py`**
Tests for `_parse_env_file` and `build_config`: basic key/value parsing, comment and blank-line handling, double- and single-quoted values, quoted values with spaces, the inline-comment trap (documents that `#` in a value is included verbatim), mismatched quotes, `Config.validate()` error cases, and `TRUENAS_BUFFERED_DOWNLOAD` bool parsing.

**`test_notifier.py`**
Tests for `Notifier`: verifies that e-mails are sent on failure when `NOTIFY_ON=failure`, suppressed when `NOTIFY_ON=never`, always sent when `NOTIFY_ON=always`, and that an SMTP exception does not propagate out of `notify()`.

**`.env.example`**
A commented template for the `.env` configuration file. All comments are on their own lines — no inline comments after values — because the parser does not strip them. Covers every supported setting with its default and a short explanation. Copy to `.env` and `chmod 600` before use.

**`.gitignore`**
Excludes `__pycache__/`, `*.pyc`, `.env` (secrets must never be committed), `.pytest_cache/`, build artifacts (`dist/`, `*.egg-info/`), virtual environments, and common IDE folders.

**`pyproject.toml`**
Build system configuration (`setuptools.build_meta`), project metadata, and the single runtime dependency (`requests`). `truenas_api_client` is intentionally not listed because it is not on PyPI and must be installed manually from a GitHub tag matching the server version. Includes `[project.scripts]` entry for the `truenas-backup` command and `[tool.pytest]` / `[tool.ruff]` configuration.

---

## Security Considerations

- The API key grants full TrueNAS API access — treat it like a root password.
- Use `TRUENAS_VERIFY_SSL=true` (default) unless your NAS uses a self-signed cert on a trusted network.
- Run the cron job as a dedicated low-privilege user that owns only the backup directory (`chmod 700`).
- The `.tar` archive contains your full system config including pool keys, SMB/NFS credentials, and certificates. The backup file is written `0600` (owner-only) automatically, but you should still protect the backup directory itself (`chmod 700`).
- If `TRUENAS_SECRET_SEED=true` (default), the archive can be used to restore the system on different hardware — store it as securely as you would a private key. At least one of `TRUENAS_SECRET_SEED` / `TRUENAS_ROOT_AUTHORIZED_KEYS` must be enabled or validation fails (TrueNAS would otherwise return a bare `.db`).

---

## Known Limitations / Future Work

- **`verify_ssl=false` requires a 25.10+ client.** With a 25.04 client the script raises a clear error with upgrade instructions. `verify_ssl=true` (the default) works with any client version.
- **Username+password auth** is not supported; API key is the recommended approach for automation and is the only method implemented.
- **`TRUENAS_API_KEY_FILE`** (reading the key from a file rather than inline) is not yet implemented. Deferred to a future pass.
- **Process lock file** is not yet implemented. Running two instances simultaneously against the same `BACKUP_DIR` is not safe. Deferred to the next pass.
- **`pool_keys`** is exposed in the `config.save` options for CORE compatibility but is a no-op on SCALE ≥ 24.x.
- **Local on-NAS execution** (via `ws+unix:///var/run/middleware/middlewared.sock`, no auth) is documented in `client.py` but not exposed via CLI — the tool assumes remote access.
- **Invalid integer env vars fall back silently.** `RETAIN_*`, `SMTP_PORT`, etc. are parsed by `_env_int`, which returns the default on a non-integer value rather than erroring. A typo like `RETAIN_DAILY=o` silently becomes `7`. The downstream `RETAIN_* >= 1` checks catch zero/negative values but not parse failures. Acceptable for now since the fallback is a sane default, but worth surfacing if stricter validation is wanted.
- **Option list lives in three places.** Each setting appears in `Config` (the dataclass), `build_config` (env parsing), `.env.example`, and this README's config table. There is no single source of truth, so adding or renaming a setting means updating all of them in lockstep. A future pass could generate the example/docs from the dataclass.

---

## Contributing

Pull requests welcome. Run `pytest` and `ruff check .` before submitting.

---

## License

MIT