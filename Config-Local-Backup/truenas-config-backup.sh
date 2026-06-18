#!/usr/bin/env bash
#
# truenas-config-backup.sh
# ------------------------
# Single-file TrueNAS SCALE system-configuration backup.
#
# Run this DIRECTLY ON the TrueNAS SCALE server as root. It uses the local
# middleware client (midclt) over the on-box socket, so NO API key is needed.
#
# What it does:
#   1. Asks middleware to build a config tar (config.save) including the
#      secret seed, and hands back a local one-time download URL.
#   2. Downloads that tar from localhost.
#   3. Saves it compressed as  <date>-<name>.tar.gz  under the backup dir.
#   4. Optionally prunes old backups beyond a retention count.
#
# The resulting archive contains freenas-v1.db and pwenc_secret (the secret
# seed). TREAT IT AS A SECRET — anyone with it can decrypt stored passwords.
#
# Usage:
#   ./truenas-config-backup.sh --dir /mnt/tank/backups
#   ./truenas-config-backup.sh --dir /mnt/tank/backups --name mynas --keep 14
#
#   --dir is REQUIRED. The script will not run without an explicit backup dir.
#
# Cron (root), daily at 01:00:
#   0 1 * * * /root/scripts/truenas-config-backup.sh --dir /mnt/tank/backups >> /var/log/truenas-config-backup.log 2>&1
#
set -euo pipefail

# Secret material is written to disk; ensure intermediate files (the .part and
# temp tar) are never group/world-readable, even before the explicit chmod 600.
umask 077

# ── defaults (override via flags or the env vars of the same name) ────────────
BACKUP_DIR="${BACKUP_DIR:-}"      # REQUIRED — no default; must be set via --dir or env
NAME="${NAME:-$(hostname -s 2>/dev/null || echo truenas)}"
KEEP="${KEEP:-0}"                 # 0 = keep everything; N = keep newest N
SECRETSEED="${SECRETSEED:-true}"  # include password secret seed (recommended)
ROOT_SSH_KEYS="${ROOT_SSH_KEYS:-false}"  # include /root/.ssh/authorized_keys

# ── helpers ──────────────────────────────────────────────────────────────────
log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

# die       — runtime failure (download, compression, etc.)   → exit 1
# usage_die — bad CLI input / usage error                     → exit 2
die()      { printf '%s  ERROR: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; exit 1; }
usage_die() { printf '%s  ERROR: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; exit 2; }

usage() {
    cat <<'EOF'
truenas-config-backup.sh — back up the TrueNAS SCALE system configuration.

Usage:
  truenas-config-backup.sh --dir <path> [options]

Options:
  --dir <path>       Required. Directory to write backups into (created if needed).
  --name <name>      Name embedded in the filename (default: short hostname).
  --keep <N>         Keep newest N backups, prune older. 0 keeps everything.
  --no-secretseed    Exclude the secret seed; requires --root-ssh-keys.
  --root-ssh-keys    Include /root/.ssh/authorized_keys in the archive.
  -h, --help         Show this help and exit.
EOF
    exit "${1:-0}"
}

# Ensure an option that expects a value actually got one.
need_value() {
    [ $# -ge 2 ] || usage_die "Option $1 requires a value."
}

# ── arg parsing ──────────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
    case "$1" in
        --dir)            need_value "$@"; BACKUP_DIR="$2"; shift 2 ;;
        --name)           need_value "$@"; NAME="$2";       shift 2 ;;
        --keep)           need_value "$@"; KEEP="$2";       shift 2 ;;
        --no-secretseed)  SECRETSEED="false"; shift ;;
        --root-ssh-keys)  ROOT_SSH_KEYS="true"; shift ;;
        -h|--help)        usage 0 ;;
        *) usage_die "Unknown argument: $1" ;;
    esac
done

# ── validate options ─────────────────────────────────────────────────────────
if [ -z "$BACKUP_DIR" ]; then
    usage_die "A backup directory is required. Pass --dir <path> (or set BACKUP_DIR)."
fi

case "$KEEP" in
    ''|*[!0-9]*) usage_die "--keep must be a non-negative integer." ;;
esac

# TrueNAS only produces a tar bundle when at least one bundle option is enabled.
# With neither, config.save returns a bare .db file — which the script would
# then mislabel as .tar.gz. Reject that combination rather than save a wrong
# file type.
if [ "$SECRETSEED" != "true" ] && [ "$ROOT_SSH_KEYS" != "true" ]; then
    usage_die "--no-secretseed requires --root-ssh-keys, otherwise TrueNAS returns a bare .db, not a tar archive."
fi

# ── preflight checks ─────────────────────────────────────────────────────────
command -v midclt >/dev/null 2>&1 || die "midclt not found — run this on the TrueNAS server."
command -v curl   >/dev/null 2>&1 || die "curl not found."
command -v jq     >/dev/null 2>&1 || die "jq not found (ships with TrueNAS SCALE)."

[ "$(id -u)" -eq 0 ] || log "WARNING: not running as root; config.save may be denied."

# Sanitise NAME for safe use in a filename: whitelist safe chars, collapse and
# trim hyphens, and fall back to a default if nothing usable remains.
NAME="$(printf '%s' "$NAME" | sed 's/[^A-Za-z0-9._-]/-/g; s/--*/-/g; s/^-//; s/-$//')"
[ -n "$NAME" ] || NAME="truenas"

mkdir -p "$BACKUP_DIR" || die "Cannot create backup dir: $BACKUP_DIR"
[ -w "$BACKUP_DIR" ] || die "Backup dir not writable: $BACKUP_DIR"

DATE="$(date '+%Y%m%d-%H%M%S')"
OUT="${BACKUP_DIR}/${DATE}-${NAME}.tar.gz"
TMP="$(mktemp "${BACKUP_DIR}/.${DATE}-${NAME}.XXXXXX.tar")"
# shellcheck disable=SC2064
trap "rm -f '$TMP'" EXIT

# ── 1. request the config archive ────────────────────────────────────────────
# core.download returns: [ <job_id>, "/_download/<id>?auth_token=<token>" ]
# The options object MUST set at least one key or middleware returns the bare
# .db file instead of a tar. We pass secretseed (and optionally root SSH keys).
log "Requesting config archive from middleware (secretseed=${SECRETSEED}, root_ssh_keys=${ROOT_SSH_KEYS})"

OPTS="$(jq -nc \
    --argjson ss "$SECRETSEED" \
    --argjson rk "$ROOT_SSH_KEYS" \
    '{secretseed: $ss, root_authorized_keys: $rk}')"

# midclt wants the params as a JSON array: [ "config.save", [ {opts} ], "filename" ]
DL_JSON="$(midclt call core.download config.save "[ ${OPTS} ]" "${DATE}-${NAME}.tar")" \
    || die "midclt core.download call failed."

DL_PATH="$(printf '%s' "$DL_JSON" | jq -r '.[1]')"
if [ -z "$DL_PATH" ] || [ "$DL_PATH" = "null" ]; then
    die "No download URL returned: $DL_JSON"
fi

# ── 2. download from localhost (token is embedded in the path) ────────────────
log "Downloading config archive"
# Use HTTP on localhost to avoid self-signed-cert hassles; the socket is local.
curl --fail --silent --show-error \
     --max-time 180 \
     "http://127.0.0.1${DL_PATH}" \
     --output "$TMP" \
    || die "Download failed from http://127.0.0.1${DL_PATH%%\?*}?…"

[ -s "$TMP" ] || die "Downloaded file is empty."

# ── 3. compress + place atomically ───────────────────────────────────────────
log "Compressing -> $OUT"
gzip -c "$TMP" > "${OUT}.part" || die "gzip failed."
mv -f "${OUT}.part" "$OUT"
chmod 600 "$OUT"   # secret material — restrict to owner

SIZE="$(du -h "$OUT" | cut -f1)"
log "Saved ${OUT} (${SIZE})"

# ── 4. retention prune ───────────────────────────────────────────────────────
if [ "$KEEP" -gt 0 ] 2>/dev/null; then
    # Newest first by filename (timestamp-prefixed names sort chronologically).
    shopt -s nullglob
    mapfile -t all < <(printf '%s\n' "${BACKUP_DIR}"/*-"${NAME}".tar.gz | sort -r)
    shopt -u nullglob
    if [ "${#all[@]}" -gt "$KEEP" ]; then
        for old in "${all[@]:$KEEP}"; do
            log "Pruning old backup: $(basename "$old")"
            rm -f "$old"
        done
    fi
fi

log "Backup cycle complete."
