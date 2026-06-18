#!/bin/sh
#
# TrueNAS SCALE custom app icon updater
# Requires mikefarah/yq v4.
#
# Default mode edits the per-app file:
#   /mnt/.ix-apps/app_configs/<APP_NAME>/metadata.yaml
#
# Optional modes:
#   ICON_MODE=global ./update-icons.sh   -> edits /mnt/.ix-apps/metadata.yaml
#   ICON_MODE=both   ./update-icons.sh   -> edits both locations
#
# After running, open the app in the TrueNAS UI, click Edit, then Save
# (no changes needed) to force the icon to refresh, then hard-refresh the
# browser with Ctrl+Shift+R.

APP_POOL="/mnt/.ix-apps"
ICON_MODE="${ICON_MODE:-per-app}"

# One entry per line: app_name|icon_url
# Lines starting with # and blank lines are ignored.
APPS='
# app_name|icon_url
YOUR_APP_NAME|https://example.com/path/to/icon.png
YOUR_APP_NAME_2|https://example.com/path/to/icon2.png
'

log() {
    printf '%s\n' "$*"
}

die() {
    log "ERROR: $*"
    exit 1
}

# Item 8: ensure the correct yq is present (mikefarah/yq v4, not the Python yq).
need_yq() {
    command -v yq >/dev/null 2>&1 || die "yq not found. Install mikefarah/yq v4 to a persistent path."
    yq --version 2>&1 | grep -qi 'mikefarah' || die "This script expects mikefarah/yq v4, not the Python yq wrapper."
}

# Item 7: timestamped backups so repeated (cron) runs never overwrite a good copy.
backup_file() {
    file="$1"
    cp -p "$file" "${file}.bak.$(date +%Y%m%d-%H%M%S)" || return 1
}

update_per_app_file() {
    app_name="$1"
    icon_url="$2"
    target_file="${APP_POOL}/app_configs/${app_name}/metadata.yaml"

    if [ ! -f "$target_file" ]; then
        log "  > [Skip] Per-app file not found: $target_file"
        return 0
    fi

    backup_file "$target_file" || {
        log "  > [Error] Could not create backup for $target_file"
        return 1
    }

    # Item 1: strenv() keeps the URL out of the yq expression, so quotes,
    # backslashes, $ and base64 data-URIs cannot break parsing.
    ICON_URL="$icon_url" yq -i '.metadata.icon = strenv(ICON_URL)' "$target_file" \
        && log "  > Per-app icon set" \
        || {
            log "  > [Error] yq failed for per-app file"
            return 1
        }
}

update_global_file() {
    app_name="$1"
    icon_url="$2"
    target_file="${APP_POOL}/metadata.yaml"

    if [ ! -f "$target_file" ]; then
        log "  > [Skip] Global metadata file not found: $target_file"
        return 0
    fi

    if ! APP_NAME="$app_name" yq -e 'has(strenv(APP_NAME))' "$target_file" >/dev/null 2>&1; then
        log "  > [Skip] App not found in global metadata: $app_name"
        return 0
    fi

    backup_file "$target_file" || {
        log "  > [Error] Could not create backup for $target_file"
        return 1
    }

    APP_NAME="$app_name" ICON_URL="$icon_url" \
        yq -i '.[strenv(APP_NAME)].metadata.icon = strenv(ICON_URL)' "$target_file" \
        && log "  > Global icon set" \
        || {
            log "  > [Error] yq failed for global metadata"
            return 1
        }
}

need_yq

# Item 6: validate mode.
case "$ICON_MODE" in
    per-app|global|both)
        ;;
    *)
        die "Invalid ICON_MODE: $ICON_MODE. Use per-app, global, or both."
        ;;
esac

log "Updating TrueNAS custom app icons - $(date)"
log "Mode: $ICON_MODE"

printf '%s\n' "$APPS" | while IFS='|' read -r APP_NAME ICON_URL; do
    # Item 2: skip blank lines and comments so the APPS block can be documented.
    case "$APP_NAME" in
        ""|\#*)
            continue
            ;;
    esac

    if [ -z "$ICON_URL" ]; then
        log "Skipping $APP_NAME: empty icon URL"
        continue
    fi

    log "Processing: $APP_NAME"

    case "$ICON_MODE" in
        per-app)
            update_per_app_file "$APP_NAME" "$ICON_URL"
            ;;
        global)
            update_global_file "$APP_NAME" "$ICON_URL"
            ;;
        both)
            update_per_app_file "$APP_NAME" "$ICON_URL"
            update_global_file "$APP_NAME" "$ICON_URL"
            ;;
    esac
done

log "Done."
log "If the icon does not appear, open the app in the TrueNAS UI, click Edit, then Save. Then hard-refresh the browser (Ctrl+Shift+R)."
