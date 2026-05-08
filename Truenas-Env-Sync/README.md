# truenas-env-sync

A small Python utility to manage environment variables for TrueNAS Scale 25 apps **outside the GUI** -- using a simple, human-editable flat file as the source of truth.

---

## The Problem

TrueNAS Scale 25 stores app configuration in **two separate YAML files** that must be kept in sync:

```
/mnt/.ix-apps/user_config.yaml                                  # global -- all apps
/mnt/.ix-apps/app_configs/<app-name>/versions/<x.x.x>/user_config.yaml  # per-app
```

When you want to set or change environment variables without going through the GUI (e.g. for automation, version control, or reproducibility), both files need to be updated with the correct structure and indentation. Doing this by hand is error-prone and tedious.

---

## The Solution

Place a flat `env_variables` file next to each app's config:

```
/mnt/.ix-apps/app_configs/<app-name>/env_variables
```

Format -- one `KEY:VALUE` per line (values may contain colons, e.g. URLs):

```
# Comments and blank lines are ignored
HOMEPAGE_VAR_URL_GLANCE_TN-PROD:http://192.168.1.22:40087
HOMEPAGE_VAR_ANOTHER_KEY:somevalue
```

Run the script and it handles everything else.

---

## How It Works

### Key concepts

| Concept | Detail |
|---|---|
| **Ignored/reserved keys** | Keys in `IGNORED_KEYS` (currently: `TZ`) are owned by TrueNAS. They are never read, written, or touched by this script. If present in `env_variables`, a warning is printed and they are skipped. Their values in both YAML files are left exactly as TrueNAS set them. |
| **`additional_envs`** | All synced env vars are stored as `[{name: KEY, value: VALUE}]` lists |
| **Source of truth** | `env_variables` wins on conflict; keys found only in the YAML are merged back into the file |
| **Auto version detection** | The script finds the latest version folder automatically -- no hardcoding needed |
| **Bootstrap mode** | If no `env_variables` file exists, it is created from the current YAML state |
| **Global YAML** | The global config is treated as an output target only. The per-app YAML is the canonical source of env state. The global YAML is TrueNAS's denormalized mirror and is always written from the merged result -- its existing `additional_envs` are not read as a separate input source. |
| **Atomic writes** | All file writes go through a sibling temp file + `os.replace()` so a crash mid-write never leaves a partial file. |
| **Partial failure safety** | Both YAMLs are parsed in memory (preflight) before either is written to disk. If either fails to parse, no files are touched. If a runtime write error occurs after one file is written, the `.bak` files can be used to restore both to a consistent state. |
| **Post-write validation** | After each YAML write, the file is re-parsed and compared to in-memory data. A mismatch prints a corruption warning and counts as a failure for that app. |
| **Safe output** | No raw values are ever printed to the terminal. Secret-looking keys (containing `password`, `token`, `secret`, `key`, `auth`, etc.) are redacted as `<redacted>` in all output. Other values are truncated at 80 characters. This applies to dry-run diffs, collision warnings, and all other diagnostic output. |

### Merge strategy

```
YAML has:   A=1, B=2
File has:   B=99, C=3

Result:     A=1 (kept from YAML, added to file)
            B=99 (file wins)
            C=3 (new from file, written to YAML)

env_variables after: B=99, C=3, A=1
```

---

## Usage

```bash
# Sync a single app (with automatic .bak backup)
sudo python3 truenas_env_sync.py homepage

# Sync ALL apps in app_configs/ in one pass
sudo python3 truenas_env_sync.py --all

# Preview changes without writing anything
sudo python3 truenas_env_sync.py homepage --dry-run
sudo python3 truenas_env_sync.py --all --dry-run

# Skip backups (not recommended on production)
sudo python3 truenas_env_sync.py homepage --no-backup

# Remove superseded comment history for specific keys
sudo python3 truenas_env_sync.py homepage --clear-history HOMEPAGE_VAR_URL_GLANCE_TN-PROD
sudo python3 truenas_env_sync.py homepage --clear-history KEY1 KEY2 KEY3
```

### First run (no `env_variables` file yet)

```
============================================================
  TrueNAS env sync  |  app: excalidraw  [DRY-RUN]
============================================================

  [INFO]  env_variables not found - will create from existing YAML config.
  Detected version : 1.2.3

>> App config: .../versions/1.2.3/user_config.yaml
>> Global config: /mnt/.ix-apps/user_config.yaml
>> Creating env_variables with 2 key(s) from YAML

[DRY-RUN] env_variables changes:
  File: .../excalidraw/env_variables
  + SOME_KEY  (YAML-only key added back)
  + ANOTHER_KEY  (YAML-only key added back)
```

The created file is immediately ready to edit and re-sync.

### --dry-run output

Dry-run prints a compact change summary instead of full file contents. No raw values are printed -- secret-looking keys are redacted, others are truncated.

```
[DRY-RUN] App YAML changes:
  File: /mnt/.ix-apps/app_configs/homepage/versions/1.3.3/user_config.yaml
  + NEW_KEY
  ~ HOMEPAGE_URL: 'http://old.host:30015' -> 'http://new.host:30015'
  ~ API_TOKEN: '<redacted>' -> '<redacted>'
  = 5 unchanged

[DRY-RUN] Global YAML changes:
  File: /mnt/.ix-apps/user_config.yaml
  ~ HOMEPAGE_URL: 'http://old.host:30015' -> 'http://new.host:30015'
  ~ API_TOKEN: '<redacted>' -> '<redacted>'
  - STALE_KEY  (removed from mirror)
  = 5 unchanged

[DRY-RUN] env_variables changes:
  File: .../homepage/env_variables
  + NEW_KEY  (YAML-only key added back)
  ~ API_TOKEN  (superseded history added)
  = 6 active key(s) unchanged
```

### --all flag

```
Found 3 app(s): ['excalidraw', 'homepage', 'vaultwarden']

============================================================
  TrueNAS env sync  |  app: excalidraw
============================================================
...

============================================================
  Summary
============================================================
  [OK]    excalidraw
  [OK]    homepage
  [FAIL]  vaultwarden
```

Apps that fail (missing versions dir, unparseable YAML, etc.) print an error and are skipped. The rest continue. Exit code is 1 if any app failed.

---

## Requirements

- Python 3.10+
- `pyyaml` -- install with:

```bash
pip install pyyaml --break-system-packages
```

---

## File Structure

```
/mnt/.ix-apps/
+-- user_config.yaml                  <- global config (all apps)
+-- app_configs/
    +-- homepage/
        +-- env_variables             <- YOUR source of truth (this tool reads/writes this)
        +-- versions/
            +-- 1.3.3/
                +-- user_config.yaml  <- per-app config (this tool reads/writes this)
```

---

## Changelog

### v0.1.0 -- Initial release
- Reads `env_variables` (KEY:VALUE flat file) and syncs into both YAML locations
- Merge strategy: file wins on conflict, YAML-only keys written back to file
- Auto-detects latest version directory
- `--dry-run` and `--no-backup` flags
- Timestamped `.bak` backups before every write

### v0.1.1 -- Bootstrap mode
- If `env_variables` does not exist, it is created from the current YAML state
- Prevents hard exit with an unhelpful error message on first run

### v0.1.3 -- Collision tracking via superseded comments
- **Feature:** When `env_variables` wins a collision against the YAML value, the old YAML value is preserved as a comment directly beneath the active line:
  ```
  TEST:OVERWRITE
  # [superseded] TEST:TEST
  ```
- History accumulates across syncs -- each new conflicting YAML value is appended as an additional comment line
- Duplicate values are never added to history twice
- If YAML and file agree in a later sync, history comments are kept as-is (not removed)
- `parse_env_file` returns `(envs, superseded)` tuple; `write_env_file` accepts `superseded` and `collisions` dicts

### v0.1.2 -- TZ regression fix
- **Bug:** `TZ` was being written into the `env_variables` file, causing it to be treated as an `additional_env` on subsequent runs
- **Root cause:** Bootstrap mode was explicitly injecting TZ into the output dict; the normal merge path was not filtering it either
- **Fix:** `write_env_file()` now unconditionally strips all `SPECIAL_TOP_LEVEL_KEYS` before writing

### v0.2.0 -- Bug fixes and TZ ownership redesign
- **Bug fix:** `merge_envs()` returns 3 values; `update_app_yaml()` was only unpacking 2, causing `ValueError` on every run
- **Bug fix:** Collision tracking was recomputed in `main()` from already-merged data, meaning the original pre-merge YAML value was lost. `update_app_yaml()` now captures and returns the collision dict from the pre-merge state and passes it through to `write_env_file()`
- **Redesign:** `SPECIAL_TOP_LEVEL_KEYS` renamed to `IGNORED_KEYS`. TZ (and any future reserved keys) are now fully out of scope -- the script never reads, writes, or modifies them anywhere. Previously the script was still actively writing TZ into both YAML files when it appeared in `file_envs`, which contradicted the stated intent
- If `TZ` (or any `IGNORED_KEYS` entry) appears in `env_variables`, a `[WARN]` is printed and the key is skipped -- no failure, no YAML modification
- `update_app_yaml()` return type hint corrected from `dict` to `tuple[dict, set, dict]`
- Docstring updated to remove misleading `TZ:Europe/Berlin` example from `env_variables` format block

### v0.3.0 -- Safety, --all, and housekeeping

- **Feature: `--all` flag** -- sync every app under `app_configs/` in one pass. Each app is processed independently; a failure on one app prints an error and continues to the next. Exit code 1 if any app failed. Summary table printed at end.
- **Feature: `--clear-history KEY [KEY ...]`** -- remove superseded comment history for specific keys. Rewrites `env_variables` with those history lines omitted. Safe to combine with `--dry-run`.
- **Atomic writes** -- all file writes (YAML and env_variables) now go through a uniquely-named sibling `.tmp` file and `os.replace()`. A crash or interruption mid-write never leaves a partial file.
- **Partial failure safety** -- both YAMLs are parsed in memory (preflight) before either is written to disk. If either parse fails, no files are touched. Runtime write failures after one file is written may still require restoring from the `.bak` files to bring both back in sync.
- **Post-write validation** -- after each YAML write, the file is re-parsed and compared to in-memory data. A mismatch prints a corruption warning and causes the app to be counted as failed.
- **Null app-block guard** -- `update_app_yaml()` and `update_global_yaml()` now check `isinstance(app_block, dict)` before calling `.get()`. A `null` or non-dict block is replaced with `{}` and a `[WARN]` is printed instead of crashing.
- **Duplicate key warning** -- `parse_env_file()` now warns when the same active key appears more than once in `env_variables`. Last value still wins, but silently no longer.
- **Global YAML drift warning** -- `update_global_yaml()` warns if the global YAML contains `additional_envs` keys absent from the per-app YAML (they are about to be removed).
- **Backup naming fix** -- backups are now named `<original_filename>.yaml.bak_YYYYMMDD_HHMMSS_NNNN`, preserving the `.yaml` extension.
- **Backup timestamp collision fix** -- a zero-padded 4-digit counter (`_NNNN`) is appended to every backup name, making them unique within a single run regardless of wall-clock resolution. Safe for `--all` + cron.
- **`latest_version_dir()` now raises** `FileNotFoundError` instead of calling `sys.exit()` directly, so `--all` can catch per-app errors and continue rather than aborting the whole run.
- **`sync_app()` extracted** -- single-app logic moved to a dedicated function that returns `True`/`False`, called by both single-app and `--all` paths.

### v0.4.0 -- Review fixes and release hardening

- **`--all` full exception isolation** -- each `sync_app()` call is now wrapped in `try/except Exception` in the `--all` loop. Write failures, permission errors, disk-full, and any other unexpected exception are caught per-app, printed as `[ERROR]`, and marked as failed in the summary. The rest of the apps continue.
- **`--clear-history` merge-back fix** -- `--clear-history` now uses the same `extended_envs` (file envs + YAML-only keys) as the normal sync path. Previously, YAML-only keys were not written back to `env_variables` when `--clear-history` was the trigger for the rewrite, violating the documented merge rule.
- **`--clear-history` collision semantic** -- when a key is both being cleared and currently colliding, the current YAML value is not re-added as a fresh superseded entry during that run. "Clear means clear now." The collision is still resolved (file wins); it just produces no history line for this run. A `[Note]` line confirms this in the output.
- **Validation failure is a hard failure** -- `update_app_yaml()` and `update_global_yaml()` return a `valid` bool. `sync_app()` checks both and returns `False` with an explicit error if either fails. Previously, a corruption warning was printed but the app was still counted as successful.
- **Unique temp filenames** -- `_unique_tmp()` produces `.<filename>.<pid>.<counter>.tmp`, making concurrent cron and manual runs safe against temp-file clobbering.
- **`os.replace()` terminology** -- all docstrings, README key-concepts table, and changelog entries now consistently say `os.replace()` instead of `os.rename()`.
- **`--clear-history` help text** -- argparse help now reads "a normal sync still runs; this only controls which history lines are kept," removing the implication that the command exits early without syncing.
- **Non-string YAML env values** -- `additional_envs_to_dict()` coerces all values to `str` (`None` becomes `""`). Non-string types print a `[WARN]` with the original value and type name. Previously, unquoted YAML scalars (`true`, `123`, `null`) could cause odd collision-detection behavior.
- **Inner global block guard** -- `update_global_yaml()` now also guards the inner `app_block[app_name]` block with an `isinstance` check and `[WARN]`, matching the outer block guard already present.
- **`load_yaml()` encoding** -- now explicitly passes `encoding="utf-8"` for consistency with all other file operations.
- **README partial-failure wording** -- key-concepts table and changelog entry both now accurately state that preflight prevents parse-related partial writes, while runtime write failures may still require restoring from `.bak` files.

### v0.4.1 -- Comment and validation flow fixes

- **Preflight comment corrected** -- the inline comment in `sync_app()` previously stated that preflight "prevents a partial write leaving the two files in a desync state," which overstated the guarantee. The comment now accurately reflects the README: preflight prevents parse-related partial updates (one file unreadable before any write begins); runtime write failures after the first file succeeds are not prevented and require restoring from `.bak` files.
- **Early exit on app YAML validation failure** -- `sync_app()` now returns `False` immediately if `update_app_yaml()` reports `app_valid=False`, skipping the `update_global_yaml()` call entirely. Previously, the global YAML was still written even when the app YAML failed post-write validation, potentially deepening the desync. The error message now explicitly states that the global write was skipped.

### v0.4.2 -- Safe dry-run output

- **Primary motivation: credential and secret protection.** Dry-run output, collision warnings, and all other diagnostic messages previously printed raw env values to the terminal. These land in cron logs, systemd journals, SSH session logs, and shell history -- leaking tokens, passwords, and URLs even when no files are written.
- **No raw values in any output path.** All terminal output now routes values through `_display_value()`. Keys whose names contain `password`, `pass`, `token`, `secret`, `key`, `api_key`, `auth`, or `credential` (case-insensitive substring match) are printed as `<redacted>`. All other values are truncated at 80 characters. This applies to dry-run diffs, collision warnings, duplicate key warnings, and non-string YAML coercion warnings.
- **Compact dry-run diff output.** `--dry-run` no longer dumps full YAML or `env_variables` contents. `save_yaml()` and `write_env_file()` print only `[DRY-RUN] Would write: <path>`. The actual change summary is generated one level up, in `update_app_yaml()`, `update_global_yaml()`, and the `env_variables` write-back paths in `sync_app()`.
- **`_print_envs_diff()`** -- new helper that prints a `+` / `-` / `~` / `= N unchanged` diff of `additional_envs` changes for both app and global YAML.
- **`_print_env_file_diff()`** -- new helper that prints a compact summary of `env_variables` changes: keys added from YAML, history entries added or cleared, and unchanged key count.
- **Malformed line warning no longer prints line content** -- `parse_env_file()` now iterates with `enumerate()` and reports only the line number (`Skipping malformed line 7 (no colon)`). A typo like `API_TOKEN=supersecret` (using `=` instead of `:`) previously echoed the full line including the secret value.

---

## Known Limitations / TODO

Items are grouped by priority.

### Medium-High

- [ ] **Version directory selection is not verified against the active version.** The script picks the numerically highest directory under `versions/` and warns if multiple exist, but does not confirm that the selected version is the one TrueNAS is actually running. If TrueNAS keeps an old active version alongside a newer staged or incomplete one, the tool could write to the wrong per-app YAML -- silently, with no obvious error until app behavior diverges. Planned improvements: print a stronger warning when multiple version dirs exist; add `--version <x.x.x>` override flag; store last-used version in a small state file and warn when it changes; optionally require explicit confirmation or `--version` when multiple dirs exist (except in `--dry-run`).

### Medium

- [ ] **Logical concurrency -- last writer wins.** Temp-file name collisions and backup name collisions are already solved. However, two concurrent sync processes can each read the same old YAML, compute changes independently, and then overwrite each other. The last writer wins and the first writer's changes are silently lost. Severity is low for a home/admin CLI tool, but before publishing cron or systemd examples, a lockfile recommendation (or implementation) should be included.
- [ ] **YAML schema assumptions -- no strict mode.** The script assumes a specific nested structure (`app_name` → `additional_envs` in the per-app YAML; double-nested `app_name` → `app_name` → `additional_envs` in the global YAML). Non-dict blocks are guarded and normalized to `{}` rather than aborting. Post-write validation confirms the written file matches the in-memory object, but cannot detect whether the in-memory object is semantically correct for a future TrueNAS schema version. Planned: add a `--strict` mode that aborts instead of normalizing when the expected structure is missing or malformed.
- [ ] **Systemd timer / cron example for automated syncing.** Include a lockfile recommendation in any published example.

### Low

- [ ] **Expand `IGNORED_KEYS` only for genuinely scalar env-like top-level keys** -- do not add structured keys like `network` or `resources`.
- [ ] **Warn if `TZ` is present in `env_variables` but file is not rewritten** (currently only warned at parse time; no cleanup prompt).
- [ ] **History accumulation over time.** `env_variables` grows as superseded history accumulates across syncs. This is intentional and already partly addressed by `--clear-history`. No structural change needed; operational maintenance issue only.

### Deferred

- [ ] **Secret detection tuning.** The substring match (`key`, `pass`, etc.) is intentionally aggressive and will redact names like `NORMAL_KEY`. False positives are annoying; false negatives leak secrets. The current conservative default is correct. Revisit only if false positives become a usability problem in practice.

---

## Contributing

PRs welcome. Please test with `--dry-run` before committing any changes that touch the YAML write paths.
