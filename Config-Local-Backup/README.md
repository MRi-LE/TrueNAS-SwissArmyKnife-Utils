# truenas-config-backup.sh

A single self-contained Bash script that backs up the **TrueNAS SCALE** system
configuration. It runs **directly on the NAS** and uses the local middleware
client (`midclt`) over the on-box socket, so **no API key is required**.

Each run produces a compressed archive named `<date>-<name>.tar.gz` in the
directory you specify, optionally pruning older backups to a retention limit.

---

## Why this approach

Most TrueNAS config-backup scripts authenticate to the REST/WebSocket API with
an API key from a remote machine. Because this script runs *on* the server, it
talks to middleware through the local socket via `midclt` — authentication is
implicit (it relies on being run as root), so there is no key to create, store,
or rotate.

The archive is built with `config.save` and downloaded from a one-time local
URL (`http://127.0.0.1/_download/...`) whose auth token is embedded in the path.

---

## What's in the backup

The archive contains your full system configuration, including:

- `freenas-v1.db` — the configuration database
- `pwenc_secret` — the secret seed (only when `secretseed` is enabled, which is
  the default)

> **⚠️ Treat the archive as a secret.** With the secret seed, anyone who has the
> file can decrypt the passwords stored in your configuration. The script writes
> every archive with `0600` permissions (owner read/write only). Store backups
> somewhere access-controlled, and be cautious about syncing them to third-party
> cloud storage unencrypted.

By default, the script includes the password secret seed. This is recommended
for real backups, because a config restored without the secret seed cannot
decrypt saved passwords.

`--no-secretseed` is only valid together with `--root-ssh-keys` in this script,
because TrueNAS only creates a tar bundle when at least one bundle option is
enabled. Without `secretseed` or `root_authorized_keys`, TrueNAS returns only
the bare database file instead of a tar archive — so the script rejects
`--no-secretseed` on its own rather than save a mislabelled file.

---

## Requirements

- TrueNAS **SCALE** (uses `midclt`, which ships with the OS).
- Run as **root** (required for `config.save`).
- `curl` and `jq` — both included in TrueNAS SCALE by default.

---

## Installation

Copy the script somewhere persistent on the NAS and make it executable. A
dataset you control is a good choice (the script itself is not secret, but its
output is):

```sh
cp truenas-config-backup.sh /mnt/tank/scripts/
chmod +x /mnt/tank/scripts/truenas-config-backup.sh
```

---

## Usage

```
truenas-config-backup.sh --dir <path> [options]
```

**`--dir` is required.** The script will not run without an explicit backup
directory — there is no default location.

### Options

| Option              | Description                                                        | Default                    |
|---------------------|--------------------------------------------------------------------|----------------------------|
| `--dir <path>`      | **Required.** Directory to write backups into. Created if missing. | *(none — must be set)*     |
| `--name <name>`     | Name embedded in the filename. Sanitised for filesystem safety.    | short hostname             |
| `--keep <N>`        | Keep only the newest `N` backups (matching `--name`); prune older. | `0` (keep everything)      |
| `--no-secretseed`   | Exclude the secret seed. Requires `--root-ssh-keys` (see below).   | seed **included**          |
| `--root-ssh-keys`   | Include `/root/.ssh/authorized_keys` in the archive.               | excluded                   |
| `-h`, `--help`      | Show usage and exit.                                               | —                          |

Every option can also be supplied via an environment variable of the same name
in uppercase (`BACKUP_DIR`, `NAME`, `KEEP`, `SECRETSEED`, `ROOT_SSH_KEYS`).
Command-line flags take precedence.

### Examples

```sh
# Minimal — write a backup into the given directory
./truenas-config-backup.sh --dir /mnt/tank/backups

# Custom name, keep only the 14 most recent
./truenas-config-backup.sh --dir /mnt/tank/backups --name mynas --keep 14

# Omit the secret seed (must pair with --root-ssh-keys to still get a tar)
./truenas-config-backup.sh --dir /mnt/tank/backups --no-secretseed --root-ssh-keys
```

---

## Output

Archives are named:

```
<YYYYMMDD-HHMMSS>-<name>.tar.gz
```

for example:

```
/mnt/tank/backups/20260618-010000-mynas.tar.gz
```

The timestamp prefix means the files sort chronologically by name, which is also
how the retention prune (`--keep`) picks the newest ones to keep.

To verify an archive:

```sh
tar -tzf /mnt/tank/backups/20260618-010000-mynas.tar.gz
# expect: freenas-v1.db  and  pwenc_secret
```

---

## Scheduling

### Cron (run as root)

Daily at 01:00, logging to a file:

```cron
0 1 * * * /mnt/tank/scripts/truenas-config-backup.sh --dir /mnt/tank/backups --keep 30 >> /var/log/truenas-config-backup.log 2>&1
```

On TrueNAS SCALE you can add this through the UI under
**System Settings → Advanced → Cron Jobs**, running as `root`.

> **This is a local backup.** For disaster recovery, replicate or copy the
> backups to another system — ideally encrypted — because a pool failure or loss
> of the NAS would also take any local-only config backups with it. TrueNAS does
> not automatically back up the system configuration, so a regular off-box copy
> (especially after configuration changes) is what makes these backups useful in
> a real recovery.

---

## Exit codes

| Code | Meaning                                                        |
|------|---------------------------------------------------------------|
| `0`  | Backup completed successfully.                                 |
| `1`  | A runtime error occurred (download, compression, etc.).       |
| `2`  | Usage error — bad or missing CLI option (e.g. no `--dir`).     |

---

## Restoring

To restore, use the TrueNAS web UI:

1. Extract the archive on your local machine to obtain `freenas-v1.db` (and
   `pwenc_secret` if present).
2. Go to **System Settings → General → Manage Configuration → Upload Config**
   and upload `freenas-v1.db`.

The secret seed (`pwenc_secret`) matters when migrating to different hardware or
recovering encrypted secrets; keep it with the matching `.db`.

---

## Troubleshooting

**`midclt not found`** — You're not on a TrueNAS server, or not in a normal root
shell. This script must run on the NAS itself.

**`config.save` validation error / only a `.db` file returned** — The TrueNAS
API requires at least one option to produce a tar. This script always passes
`secretseed` (or `root_authorized_keys`) so this shouldn't happen; if it does on
your version, the option-object quoting that `midclt` expects may differ. As a
fallback you can use the local REST endpoint with a bearer token:

```sh
curl -X POST "http://localhost/api/v2.0/config/save" \
     -H "Authorization: Bearer <API_KEY>" \
     -H "Content-Type: application/json" \
     -d '{"secretseed": true}' \
     -o backup.tar
```

**Permission denied writing the backup** — Ensure `--dir` points to a writable
path and that you're running as root.

---

## Disclaimer

Provided as-is, without warranty. The configuration archive contains sensitive
material; you are responsible for storing it securely. Always test a restore
before relying on a backup.
