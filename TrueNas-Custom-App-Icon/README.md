# TrueNAS SCALE — Custom App Icon Updater

A small POSIX `sh` script that sets custom icons for **custom apps** in TrueNAS SCALE
(Electric Eel 24.10+ / 25.04+). TrueNAS has no UI for this, so the icon must be written
directly into the app's `metadata.yaml`. App updates regenerate that file and wipe the
icon, so this script is designed to be **re-run on a schedule** to keep icons in place.

## Why this is needed

TrueNAS SCALE stores custom-app metadata in:

```
/mnt/.ix-apps/app_configs/<APP_NAME>/metadata.yaml
```

There is no supported way to set a custom-app icon from the web UI. Editing this file is
a community workaround, not an official feature — a TrueNAS update or an **app update**
can regenerate the file and remove your icon. Scheduling the script (cron) re-applies the
icon automatically after those events.

### Which file: per-app vs global

On many systems the per-app file above is the correct place to edit. On some systems —
especially depending on TrueNAS version or upgrade history — the global file
`/mnt/.ix-apps/metadata.yaml` is the only location that affects the UI. Start with the
default per-app mode. If nothing changes after Edit → Save and a browser refresh, try
`ICON_MODE=global` or `ICON_MODE=both` (see [Modes](#modes)).

## Requirements

- TrueNAS SCALE with custom (Docker/compose) apps.
- Root shell access (System Settings → Shell, or SSH).
- `yq` — specifically **mikefarah/yq v4** (the Go implementation), not the Python `yq`
  wrapper. The script checks for this and refuses to run with the wrong one.

## Setup

1. **Find your app name.** It is the directory name under `/mnt/.ix-apps/app_configs/`:

   ```sh
   ls /mnt/.ix-apps/app_configs/
   ```

2. **Edit the `APPS` block.** One entry per line as `app_name|icon_url`. Blank lines and
   lines starting with `#` are ignored, so you can annotate the list.

3. **Save the script** to a persistent dataset (not under `/mnt/.ix-apps`, which TrueNAS
   manages):

   ```sh
   nano /mnt/yourpool/scripts/update-icons.sh
   chmod +x /mnt/yourpool/scripts/update-icons.sh
   ```

4. **Run it:**

   ```sh
   /mnt/yourpool/scripts/update-icons.sh
   ```

5. **Force the refresh.** Editing the YAML alone is not enough. In the web UI, open each
   app, click **Edit**, and **Save without changing anything** to force the app to pick up
   the new icon. Then hard-refresh the browser with **Ctrl+Shift+R**.

## Modes

The script defaults to editing the per-app file. Override with the `ICON_MODE`
environment variable:

```sh
# per-app file only (default)
/mnt/yourpool/scripts/update-icons.sh

# global /mnt/.ix-apps/metadata.yaml only
ICON_MODE=global /mnt/yourpool/scripts/update-icons.sh

# both locations
ICON_MODE=both /mnt/yourpool/scripts/update-icons.sh
```

In `global`/`both` mode the script only touches the global file if it already contains an
entry for the named app; otherwise it skips it safely.

## Scheduling (keep icons after app updates)

Because an app update regenerates `metadata.yaml`, schedule the script so it re-applies:

- TrueNAS UI: **System Settings → Advanced → Cron Jobs → Add**
- Command: `/mnt/yourpool/scripts/update-icons.sh`
- Run as: `root`
- Schedule: e.g. hourly or daily, to taste.

After a scheduled run you may still need the one-time UI edit+save / browser refresh for
the change to become visible.

## Choosing an icon URL

The URL must return the **image itself**, not a web page that contains it.

- **GitHub `/blob/` links do NOT work.** A URL like
  `https://github.com/user/repo/blob/main/icon.png` serves an HTML page, and the icon will
  render as a broken image. Use the raw form instead:
  `https://raw.githubusercontent.com/user/repo/main/icon.png`, or append `?raw=true` to a
  blob URL. (This is a documented failure case from the forum thread.)
- **Icon libraries:** direct image/SVG URLs from sources like selfh.st/icons work well.
- **Self-hosted (most reliable):** serve the images from the NAS itself (e.g. behind a
  Caddy/Nginx reverse proxy). This avoids broken external links and loads fast. Base64
  data-URIs embedded in the YAML also work but do **not** survive app updates.

## Backups

The script writes a **timestamped** backup next to each file before editing it, e.g.
`metadata.yaml.bak.20260618-124500`. This means repeated (cron) runs never overwrite a
known-good copy. The trade-off is that these backups accumulate over time; prune them
periodically if you run the script on a frequent schedule.

If an app fails to load after an edit, restore the most recent good backup:

```sh
cp /mnt/.ix-apps/app_configs/<APP_NAME>/metadata.yaml.bak.<timestamp> \
   /mnt/.ix-apps/app_configs/<APP_NAME>/metadata.yaml
```

## Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| Icon doesn't change after running the script | You must trigger an app update: UI → Edit → Save, then Ctrl+Shift+R. |
| Icon shows a broken image | The URL points to an HTML page (e.g. a GitHub `/blob/` link), not a raw image. Use a direct image URL. |
| Icon disappears after an app update | Expected. Schedule the script via cron to re-apply. |
| Per-app edits do nothing | Your system may only honor the global file. Try `ICON_MODE=global` or `ICON_MODE=both`. |
| `yq not found` / wrong-yq error | Install mikefarah/yq v4 to a persistent path; the Python `yq` is not compatible. |
| App fails to load after editing | Malformed YAML. Restore from the timestamped `.bak` file (see [Backups](#backups)). |

## Optional hardening

These are not enabled by default to keep the script lean, but you may want them in some
setups.

### App-name safety check

The app names come from the hand-edited `APPS` block, run as root, so the realistic risk
is a typo (which simply produces a "file not found" skip). If you still want to guard
against an app name accidentally containing path characters, add this helper and call it
in the loop before processing each entry:

```sh
valid_app_name() {
    case "$1" in
        ""|*/*|*..*) return 1 ;;
        *)           return 0 ;;
    esac
}
```

```sh
    if ! valid_app_name "$APP_NAME"; then
        log "Skipping invalid app name: $APP_NAME"
        continue
    fi
```

This refuses any app name containing `/` or `..`, so a typo cannot point outside the
expected app directory.

### Emergency no-yq fallback (per-app only)

`yq` is not preinstalled on TrueNAS and lives on an overlay that updates can wipe. If you
are ever stuck without it, this `sed`-based snippet sets the icon on the **per-app file
only**. Do **not** run it against the global file — `sed` is easy to break there if app
names, indentation, or YAML structure differ.

```sh
TARGET_FILE="/mnt/.ix-apps/app_configs/<APP_NAME>/metadata.yaml"
cp -p "$TARGET_FILE" "${TARGET_FILE}.bak.$(date +%Y%m%d-%H%M%S)"
if grep -q '"icon":' "$TARGET_FILE"; then
    sed -i "s@\"icon\": \"[^\"]*\"@\"icon\": \"<ICON_URL>\"@" "$TARGET_FILE"
else
    sed -i "/^[[:space:]]*\"metadata\":/a \\  \"icon\": \"<ICON_URL>\"" "$TARGET_FILE"
fi
```

Prefer the `yq` script for anything routine; this is an emergency stopgap only.

## Safety notes

- Editing files under `/mnt/.ix-apps` is an unsupported workaround. Keep your script and
  icon URLs handy so you can re-apply after TrueNAS upgrades.
- Prefer self-hosted icons to avoid dependence on external links that may break.

## Credits

Based on community findings in the TrueNAS forum thread "How to change icon of custom
app?", notably the scheduled-script approach shared by Friday_Anubis and qwerty0007,
hardened here with `strenv`-safe `yq` editing, mode selection, timestamped backups, and a
yq-implementation guard.

Thread: https://forums.truenas.com/t/how-to-change-icon-of-custom-app/24789
