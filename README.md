# TrueNAS SwissArmyKnife Utils

A collection of small, focused, production-quality utilities for **TrueNAS SCALE** (and, where noted, CORE). Each tool solves one specific gap in day-to-day TrueNAS administration — config backups, custom app icons, and out-of-GUI environment variable management — and lives in its own self-contained subdirectory with its own README.

| Utility | What it does | Language |
|---|---|---|
| [Config-API-Backup](#config-api-backup) | Automated daily config backups over the WebSocket JSON-RPC API, with 3-tier retention | Python |
| [Config-Local-Backup](#config-local-backup) | On-box config backup using the local `midclt` socket — no API key needed | Bash |
| [TrueNas-Custom-App-Icon](#truenas-custom-app-icon) | Sets and re-applies custom icons for custom apps (no UI for this in TrueNAS) | POSIX `sh` |
| [Truenas-Env-Sync](#truenas-env-sync) | Manages app environment variables outside the GUI from a flat, editable file | Python |

---

## Config-API-Backup

📁 **[Config-API-Backup/](https://github.com/MRi-LE/TrueNAS-SwissArmyKnife-Utils/tree/main/Config-API-Backup)**

A Python tool for automated daily backups of TrueNAS SCALE / CORE system configuration via the **WebSocket JSON-RPC API**, with three-tier (daily / weekly / monthly) retention. Designed to run as a cron job or systemd timer on any host with network access to the NAS.

**Highlights**
- Uses the official `truenas_api_client` library over a single persistent connection per run (respects TrueNAS's auth rate limit).
- Three backup tiers — daily, weekly (Sundays), monthly (1st of the month) — each with configurable retention.
- Timestamp-based pruning from the `YYYYMMDD-HHMMSS` in the filename, so it stays correct after copies/`rsync`.
- Atomic writes (`.tmp` → rename), dry-run mode, optional SMTP e-mail notifications, and a rotating log file.
- Legacy support via `TRUENAS_LEGACY_WS=true` for TrueNAS ≤ 24.10 / CORE.
- All config through `.env` / environment variables — no secrets in scripts or cron entries.

**Requires** Python ≥ 3.9, `requests`, and `truenas_api_client` (installed from the GitHub tag matching your TrueNAS version). The config archive contains secrets when the secret seed is enabled — backup files are written `0600`; protect the backup directory accordingly.

---

## Config-Local-Backup

📁 **[Config-Local-Backup/](https://github.com/MRi-LE/TrueNAS-SwissArmyKnife-Utils/tree/main/Config-Local-Backup)**

A single self-contained Bash script (`truenas-config-backup.sh`) that backs up the TrueNAS SCALE configuration **directly on the NAS** using the local middleware client (`midclt`) over the on-box socket — so **no API key is required**. Authentication is implicit (run as root), so there's no key to create, store, or rotate.

**Highlights**
- Each run produces a compressed `<date>-<name>.tar.gz` archive in a directory you specify.
- Optional retention pruning via `--keep N`.
- Includes the password secret seed (`pwenc_secret`) by default — recommended for real backups.
- Archives are written `0600` (owner read/write only); the script treats them as secret material.

**Requires** TrueNAS SCALE (ships with `midclt`, `curl`, `jq`), run as root. `--dir` is mandatory; there is no default location. Pairs well with a TrueNAS cron job, plus an off-box copy for disaster recovery.

---

## TrueNas-Custom-App-Icon

📁 **[TrueNas-Custom-App-Icon/](https://github.com/MRi-LE/TrueNAS-SwissArmyKnife-Utils/tree/main/TrueNas-Custom-App-Icon)**

A small POSIX `sh` script that sets custom icons for **custom apps** in TrueNAS SCALE (Electric Eel 24.10+ / 25.04+). TrueNAS has no UI for this, so the icon is written directly into the app's `metadata.yaml`. Because app updates regenerate that file and wipe the icon, the script is designed to be **re-run on a schedule** (cron) to keep icons in place.

**Highlights**
- Edits the per-app `metadata.yaml` by default; `ICON_MODE=global` or `ICON_MODE=both` for systems that only honor the global file.
- Simple `app_name|icon_url` list, with comment/blank-line support.
- Timestamped backups before every edit, plus an emergency `sed`-based fallback for when `yq` is missing.
- Guidance on choosing icon URLs (raw image URLs only — GitHub `/blob/` links render broken).

**Requires** root shell access and **mikefarah/yq v4** (the Go implementation — the script refuses to run with the Python `yq`). Editing files under `/mnt/.ix-apps` is an unsupported community workaround.

---

## Truenas-Env-Sync

📁 **[Truenas-Env-Sync/](https://github.com/MRi-LE/TrueNAS-SwissArmyKnife-Utils/tree/main/Truenas-Env-Sync)**

A Python utility (`truenas_env_sync.py`) to manage environment variables for TrueNAS SCALE 25 apps **outside the GUI**, using a simple human-editable flat file (`env_variables`) as the source of truth. TrueNAS keeps app config in two separate YAML files that must stay in sync; this tool keeps them consistent for you.

**Highlights**
- Flat `KEY:VALUE` source file (values may contain colons, e.g. URLs); comments and blank lines ignored.
- Merge strategy: the file wins on conflict, and YAML-only keys are merged back into the file.
- Auto-detects the latest version directory; bootstrap mode creates `env_variables` from current YAML on first run.
- Atomic writes, preflight parsing, and post-write validation for safety; timestamped `.bak` backups.
- `--all` (sync every app), `--dry-run` (preview), and `--clear-history` flags.
- Safe output: secret-looking keys are redacted and values truncated, so nothing leaks into cron/journald logs.
- Reserved keys (e.g. `TZ`) are owned by TrueNAS and never touched.

**Requires** Python 3.10+ and `pyyaml`. Run with `sudo`. Always test with `--dry-run` before committing changes to the YAML write paths.

---

## License

Individual utilities specify their own license in their subdirectory (e.g. Config-API-Backup is MIT). See each subproject's README for details.

## Contributing

PRs welcome. Please test changes against the relevant utility's guidance (e.g. `--dry-run` for the Python tools, `pytest` / `ruff check .` for Config-API-Backup) before submitting.
