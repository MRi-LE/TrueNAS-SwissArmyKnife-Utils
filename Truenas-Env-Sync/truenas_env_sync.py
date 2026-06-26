#!/usr/bin/env python3
"""
truenas_env_sync.py
---
Syncs environment variables from a local `env_variables` file into the two
TrueNAS Scale YAML config files that track app configuration:

  1. /mnt/.ix-apps/user_config.yaml          (global, all apps)
  2. /mnt/.ix-apps/app_configs/<app>/versions/<latest>/user_config.yaml

Merge strategy
--------------
- Existing additional_envs are read from the YAML files.
- The env_variables file is the source of truth for values:
    -> Keys present in env_variables overwrite YAML values.
    -> Keys present in YAML but NOT in env_variables are kept AND written
      back into env_variables so the file stays complete.
- IGNORED_KEYS (e.g. TZ) are never read, written, or touched in any way.
  If they appear in env_variables they are warned and skipped.
  Their values in the YAML files are left exactly as TrueNAS set them.
- Everything else lives inside  <appname>.additional_envs[].

Usage
-----
    python3 truenas_env_sync.py <app_name> [--dry-run] [--no-backup]
    python3 truenas_env_sync.py --all       [--dry-run] [--no-backup]
    python3 truenas_env_sync.py <app_name> --clear-history KEY [KEY ...]

Version: v0.4.3

    app_name        : e.g. "homepage"
    --all           : sync every app found in APP_CONFIGS_ROOT
    --dry-run       : print what would change, write nothing
    --no-backup     : skip .bak files (not recommended on production)
    --clear-history : remove superseded comment history for the given key(s)

env_variables file location
-----------
    /mnt/.ix-apps/app_configs/<app_name>/env_variables

Format (one entry per line, values may contain colons):
    # comment lines and blank lines are ignored
    HOMEPAGE_VAR_URL_GLANCE_TN-PROD:http://192.168.178.105:30015
    HOMEPAGE_VAR_ANOTHER_KEY:somevalue

Note: Keys in IGNORED_KEYS (currently: TZ) are reserved for TrueNAS.
They must not appear in env_variables. If present, they will be warned
and skipped -- the YAML value is never read or modified by this script.
"""

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml

# --- Constants ---

GLOBAL_CONFIG = Path("/mnt/.ix-apps/user_config.yaml")
APP_CONFIGS_ROOT = Path("/mnt/.ix-apps/app_configs")
# Keys owned by TrueNAS. Never read from env_variables, never written to
# either YAML file, never included in additional_envs. Warn and skip if
# found in env_variables.
IGNORED_KEYS = {"TZ"}

# Per-run counters -- incremented for every temp file and backup created.
# Keeps names unique within a single run regardless of wall-clock resolution,
# which matters for --all + cron where many files are written per second.
_tmp_counter    = 0
_backup_counter = 0


def _unique_tmp(path: Path) -> Path:
    """Return a unique sibling temp path safe across concurrent processes."""
    global _tmp_counter
    _tmp_counter += 1
    return path.parent / f".{path.name}.{os.getpid()}.{_tmp_counter:04d}.tmp"


# --- Dry-run diff helpers ---

# Substrings that mark a key as secret-bearing. Case-insensitive match.
_SECRET_SUBSTRINGS = {
    "password", "pass", "token", "secret", "key", "api_key", "auth", "credential",
}
_DRY_RUN_VALUE_MAX = 80   # truncation limit for non-secret values


def _is_secret_key(key: str) -> bool:
    """Return True if the key name suggests it holds a secret value."""
    lower = key.lower()
    return any(s in lower for s in _SECRET_SUBSTRINGS)


def _display_value(key: str, value: str) -> str:
    """Return a safe display string for a value: redact secrets, truncate others."""
    if _is_secret_key(key):
        return "<redacted>"
    if len(value) > _DRY_RUN_VALUE_MAX:
        return value[:_DRY_RUN_VALUE_MAX] + "..."
    return value


def _print_envs_diff(label: str, path: Path, before: dict, after: dict) -> None:
    """
    Print a compact dry-run diff of additional_envs changes.
    before / after are plain {KEY: VALUE} dicts.
    Used for both app YAML and global YAML.
    """
    added   = sorted(k for k in after  if k not in before)
    removed = sorted(k for k in before if k not in after)
    changed = sorted(
        k for k in after
        if k in before and after[k] != before[k]
    )
    unchanged_count = sum(
        1 for k in after if k in before and after[k] == before[k]
    )

    print(f"\n[DRY-RUN] {label}:")
    print(f"  File: {path}")
    for k in added:
        print(f"  + {k}")
    for k in removed:
        print(f"  - {k}  (removed from mirror)")
    for k in changed:
        old = _display_value(k, before[k])
        new = _display_value(k, after[k])
        print(f"  ~ {k}: '{old}' -> '{new}'")
    if unchanged_count:
        print(f"  = {unchanged_count} unchanged")
    if not (added or removed or changed):
        print("  (no additional_envs changes)")


def _print_env_file_diff(
    path: Path,
    before_envs: dict,
    after_envs: dict,
    before_superseded: dict,
    new_history: dict,
    clear_history: set,
    cleared_and_colliding: set,
) -> None:
    """
    Print a compact dry-run summary of what would change in env_variables.

    before_envs         : active keys before this run
    after_envs          : active keys that will be written
    before_superseded   : existing superseded history {key: [values]}
    new_history         : {key: yaml_value} -- new collision entries that would be added
    clear_history       : keys whose history is being wiped
    cleared_and_colliding : keys being cleared that also have a current collision

    Note: active keys are never removed during a sync (env_variables only grows
    or stays the same). The diff therefore has no removed-key branch.
    """
    added_keys = sorted(k for k in after_envs if k not in before_envs)
    unchanged  = sum(1 for k in after_envs if k in before_envs)

    print(f"\n[DRY-RUN] env_variables changes:")
    print(f"  File: {path}")

    for k in added_keys:
        print(f"  + {k}  (YAML-only key added back)")
    for k in sorted(new_history):
        if k not in clear_history:
            print(f"  ~ {k}  (superseded history added)")
    for k in sorted(clear_history):
        if k in cleared_and_colliding:
            print(f"  - {k}  (history cleared; current YAML collision not re-added)")
        elif before_superseded.get(k):
            print(f"  - {k}  (history cleared)")
    if unchanged:
        print(f"  = {unchanged} active key(s) unchanged")
    if not (added_keys or new_history or clear_history):
        print("  (no env_variables changes)")


# --- YAML helpers ---

def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def save_yaml(path: Path, data: dict, dry_run: bool = False) -> None:
    """Write YAML atomically via a temp file + os.replace()."""
    content = yaml.dump(
        data,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        indent=2,
    )
    if dry_run:
        print(f"  [DRY-RUN] Would write: {path}")
        return

    tmp = _unique_tmp(path)
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)   # POSIX atomic replace
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    print(f"  [OK]  Written: {path}")


def validate_yaml(path: Path, expected_data: dict) -> bool:
    """
    Re-parse path and compare to expected_data.
    Returns True if they match, False (with warning) otherwise.

    Assumes PyYAML round-trips the data identically (serialize -> parse -> same dict).
    This holds for str-only env values but could produce false corruption warnings
    for unusual scalar types (e.g. floats, booleans) if they ever appear in the
    YAML outside of additional_envs. In practice this tool only writes str values,
    so the assumption is stable.
    """
    try:
        on_disk = load_yaml(path)
    except Exception as exc:
        print(f"  [WARN]  Post-write validation failed (parse error): {exc}")
        return False
    if on_disk != expected_data:
        print(f"  [WARN]  Post-write validation MISMATCH for {path} -- file may be corrupt!")
        return False
    return True


def backup(path: Path) -> None:
    global _backup_counter
    _backup_counter += 1
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Preserve the original extension (.yaml) and append the backup suffix.
    # Add a counter so rapid successive calls (--all / cron) never collide.
    suffix = f".bak_{ts}_{_backup_counter:04d}"
    dest = path.parent / (path.name + suffix)
    shutil.copy2(path, dest)
    print(f"  [BAK] Backup : {dest}")


# --- env_variables file helpers ---

SUPERSEDED_PREFIX = "# [superseded] "


def parse_env_file(env_file: Path) -> tuple[dict, dict]:
    """
    Read KEY:VALUE lines (split on first colon).
    Also collects existing [superseded] comment lines per key.

    Warns on:
      - Malformed lines (no colon)
      - Duplicate active keys (last value wins, but a warning is printed)
      - IGNORED_KEYS entries

    Returns:
        envs        : {KEY: value}  -- active entries only
        superseded  : {KEY: [old_value, ...]}  -- history comments per key
    """
    envs = {}
    superseded = {}   # key -> list of already-known superseded values
    last_key = None

    for lineno, raw in enumerate(env_file.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            last_key = None
            continue
        if line.startswith(SUPERSEDED_PREFIX):
            # Parse the payload after the prefix: KEY:VALUE
            payload = line[len(SUPERSEDED_PREFIX):]
            if ":" in payload and last_key:
                sup_key, _, sup_val = payload.partition(":")
                if sup_key.strip() == last_key:
                    superseded.setdefault(last_key, []).append(sup_val.strip())
            continue
        if line.startswith("#"):
            continue  # other comments - ignore
        if ":" not in line:
            print(f"  [WARN]  Skipping malformed line {lineno} (no colon)")
            last_key = None
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if key in IGNORED_KEYS:
            print(f"  [WARN]  Ignoring reserved key {key!r} in env_variables"
                  f" -- managed by TrueNAS, not this script")
            last_key = None
            continue
        if key in envs:
            print(f"  [WARN]  Duplicate active key {key!r} in env_variables"
                  f" -- keeping last value ({_display_value(key, value.strip())!r}),"
                  f" was ({_display_value(key, envs[key])!r})")
        envs[key] = value.strip()
        last_key = key

    return envs, superseded


def write_env_file(
    env_file: Path,
    envs: dict,
    superseded: dict = None,
    collisions: dict = None,
    clear_history: set = None,
    dry_run: bool = False,
) -> None:
    """
    Write env_variables atomically via a temp file + os.replace().

    - IGNORED_KEYS (e.g. TZ) are never written -- enforced as a last-resort
      safeguard even if a caller passes them in.
    - After each active KEY:VALUE line, existing superseded history is written,
      and any new collision (yaml_value != file_value) is appended to that history.
    - Keys listed in clear_history have their superseded history omitted entirely.

    superseded    : {KEY: [old_val, ...]}  -- already known history from last parse
    collisions    : {KEY: yaml_value}      -- new values from YAML that differ from file
    clear_history : set of KEYs whose history should be wiped
    """
    superseded = superseded or {}
    collisions = collisions or {}
    clear_history = clear_history or set()
    lines = []

    for key, value in envs.items():
        if key in IGNORED_KEYS:
            continue

        lines.append(f"{key}:{value}")

        if key in clear_history:
            continue  # drop all history for this key

        # Gather history: existing entries first, then new collision if any
        history = list(superseded.get(key, []))
        if key in collisions:
            yaml_val = collisions[key]
            # Only append if this exact value is not already in history
            if yaml_val != value and yaml_val not in history:
                history.append(yaml_val)

        for old_val in history:
            lines.append(f"{SUPERSEDED_PREFIX}{key}:{old_val}")

    content = "\n".join(lines) + "\n"

    if dry_run:
        print(f"  [DRY-RUN] Would write: {env_file}")
        return

    tmp = _unique_tmp(env_file)
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, env_file)   # POSIX atomic replace
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    print(f"  [OK]  env_variables updated: {env_file}")


# --- additional_envs helpers ---

def additional_envs_to_dict(additional_envs: list) -> dict:
    """Convert [{name: K, value: V}, ...] -> {K: V} with values normalized to str.

    PyYAML may load unquoted YAML scalars (true, 123, null) as bool/int/None.
    All env values are coerced to str so collision detection against string
    values from env_variables behaves correctly. None becomes "".
    """
    result = {}
    for entry in additional_envs or []:
        if isinstance(entry, dict) and "name" in entry:
            raw_val = entry.get("value", "")
            if raw_val is None:
                str_val = ""
            else:
                str_val = str(raw_val)
                if not isinstance(raw_val, str):
                    print(f"  [WARN]  Non-string YAML value for {entry['name']!r}:"
                          f" ({type(raw_val).__name__}) -- coerced to"
                          f" {_display_value(entry['name'], str_val)!r}")
            result[entry["name"]] = str_val
    return result


def dict_to_additional_envs(d: dict) -> list:
    """Convert {K: V} -> [{name: K, value: V}, ...]  (sorted for stability)"""
    return [{"name": k, "value": v} for k, v in sorted(d.items())]


def merge_envs(from_yaml: dict, from_file: dict) -> tuple[dict, set, dict]:
    """
    Merge two {KEY: VALUE} dicts.
    - from_file wins on conflicts.
    Returns:
        merged     : combined dict (file wins)
        new_keys   : keys present in YAML but not in file (will be added to file)
        collisions : {key: yaml_value} for keys in both where values differ
                     (file value wins, yaml value is recorded as superseded)
    """
    merged = dict(from_yaml)        # start with everything already in YAML
    merged.update(from_file)        # file wins on conflict
    new_keys = set(from_yaml) - set(from_file)
    collisions = {
        k: from_yaml[k]
        for k in from_yaml
        if k in from_file and from_yaml[k] != from_file[k]
    }
    return merged, new_keys, collisions


# --- Version detection ---

def latest_version_dir(app_name: str) -> Path:
    versions_root = APP_CONFIGS_ROOT / app_name / "versions"
    if not versions_root.exists():
        raise FileNotFoundError(f"versions directory not found: {versions_root}")

    candidates = [d for d in versions_root.iterdir() if d.is_dir()]
    if not candidates:
        raise FileNotFoundError(f"no version directories found under {versions_root}")

    if len(candidates) > 1:
        print(f"  [WARN]  Multiple version directories found under {versions_root}:"
              f" {sorted(c.name for c in candidates)} -- using latest")

    def version_tuple(p: Path):
        try:
            return tuple(int(x) for x in p.name.split("."))
        except ValueError:
            return (0,)

    return max(candidates, key=version_tuple)


# --- Core update logic ---

def preflight_yaml(path: Path, label: str) -> dict:
    """
    Load and return YAML data, raising on any parse error before any writes occur.
    Part of partial-failure safety: both YAMLs are validated in memory before
    either is written to disk.
    """
    try:
        return load_yaml(path)
    except Exception as exc:
        raise RuntimeError(f"Preflight parse failed for {label} ({path}): {exc}") from exc


def update_app_yaml(
    app_yaml_path: Path,
    app_name: str,
    file_envs: dict,
    dry_run: bool,
    do_backup: bool,
    preloaded_data: dict = None,
) -> tuple[dict, set, dict, bool]:
    """
    Update the app-specific user_config.yaml.

    preloaded_data : already-parsed YAML dict from preflight (avoids re-reading).

    Returns:
        merged_additional : merged additional_envs dict (re-used in global config)
        new_from_yaml     : keys found in YAML but absent from env_variables
        collisions        : {key: original_yaml_value} where file value won;
                            captured before merge so the pre-merge YAML value
                            is available for superseded-comment tracking
        valid             : False if post-write validation failed
    """
    print(f"\n>> App config: {app_yaml_path}")
    data = preloaded_data if preloaded_data is not None else load_yaml(app_yaml_path)

    # -- null app-block guard ------------------
    raw_block = data.get(app_name)
    app_block = raw_block if isinstance(raw_block, dict) else {}
    if raw_block is not None and not isinstance(raw_block, dict):
        print(f"  [WARN]  app block for {app_name!r} is not a dict (got {type(raw_block).__name__})"
              f" -- treating as empty")

    yaml_add_envs = additional_envs_to_dict(app_block.get("additional_envs", []))

    # Strip IGNORED_KEYS before merge -- they must never enter additional_envs.
    # parse_env_file already warns and drops them, so this is a safeguard only.
    non_ignored_file = {k: v for k, v in file_envs.items() if k not in IGNORED_KEYS}

    merged, new_from_yaml, collisions = merge_envs(yaml_add_envs, non_ignored_file)

    if new_from_yaml:
        print(f"  [INFO]  Keys in YAML not in env_variables (will be added to file): {sorted(new_from_yaml)}")

    app_block["additional_envs"] = dict_to_additional_envs(merged)
    data[app_name] = app_block

    if dry_run:
        _print_envs_diff("App YAML changes", app_yaml_path, yaml_add_envs, merged)

    if do_backup and not dry_run:
        backup(app_yaml_path)

    save_yaml(app_yaml_path, data, dry_run)

    valid = True
    if not dry_run:
        valid = validate_yaml(app_yaml_path, data)

    return merged, new_from_yaml, collisions, valid


def update_global_yaml(
    global_path: Path,
    app_name: str,
    merged_additional: dict,
    dry_run: bool,
    do_backup: bool,
    preloaded_data: dict = None,
) -> bool:
    """Update the global user_config.yaml.

    Only additional_envs is written. IGNORED_KEYS (e.g. TZ) in the global
    YAML are left exactly as TrueNAS set them.

    Warns if the global YAML contains additional_envs keys not present in
    the per-app YAML (they are about to be silently overwritten).

    preloaded_data : already-parsed YAML dict from preflight (avoids re-reading).

    Returns:
        valid : False if post-write validation failed
    """
    print(f"\n>> Global config: {global_path}")
    data = preloaded_data if preloaded_data is not None else load_yaml(global_path)

    # -- null app-block guard ------------------
    raw_block = data.get(app_name)
    app_block = raw_block if isinstance(raw_block, dict) else {}
    if raw_block is not None and not isinstance(raw_block, dict):
        print(f"  [WARN]  global app block for {app_name!r} is not a dict"
              f" (got {type(raw_block).__name__}) -- treating as empty")

    # -- additional_envs -----------------------
    raw_inner = app_block.get(app_name)
    inner = raw_inner if isinstance(raw_inner, dict) else {}
    if raw_inner is not None and not isinstance(raw_inner, dict):
        print(f"  [WARN]  global inner block for {app_name!r}[{app_name!r}] is not a dict"
              f" (got {type(raw_inner).__name__}) -- treating as empty")

    # Drift warning: keys in global YAML not in merged result will be overwritten.
    global_existing = additional_envs_to_dict(inner.get("additional_envs", []))
    drift_keys = set(global_existing) - set(merged_additional)
    if drift_keys:
        print(f"  [WARN]  Global YAML has additional_envs keys absent from per-app YAML"
              f" (will be removed): {sorted(drift_keys)}")

    inner["additional_envs"] = dict_to_additional_envs(merged_additional)
    app_block[app_name] = inner
    data[app_name] = app_block

    if dry_run:
        _print_envs_diff("Global YAML changes", global_path, global_existing, merged_additional)

    if do_backup and not dry_run:
        backup(global_path)

    save_yaml(global_path, data, dry_run)

    valid = True
    if not dry_run:
        valid = validate_yaml(global_path, data)

    return valid


# --- Single-app sync ---

def sync_app(
    app_name: str,
    dry_run: bool,
    do_backup: bool,
    clear_history: set = None,
) -> bool:
    """
    Sync one app. Returns True on success, False on non-fatal error
    (so --all can continue to the next app).
    """
    clear_history = clear_history or set()

    print(f"\n{'=' * 60}")
    print(f"  TrueNAS env sync  |  app: {app_name}{'  [DRY-RUN]' if dry_run else ''}")
    print(f"{'=' * 60}")

    # -- Locate env_variables file -------------
    env_file = APP_CONFIGS_ROOT / app_name / "env_variables"

    if not env_file.exists():
        print(f"\n  [INFO]  env_variables not found - will create from existing YAML config.")
        file_envs = {}
        superseded = {}
        bootstrap_mode = True
    else:
        file_envs, superseded = parse_env_file(env_file)
        bootstrap_mode = False
        print(f"\n  Loaded {len(file_envs)} variable(s) from env_variables")

    # -- Locate app-specific YAML --------------
    try:
        version_dir = latest_version_dir(app_name)
    except FileNotFoundError as exc:
        print(f"\n  [ERROR]  {exc}")
        return False

    app_yaml = version_dir / "user_config.yaml"
    print(f"  Detected version : {version_dir.name}")

    if not app_yaml.exists():
        print(f"\n  [ERROR]  app user_config.yaml not found: {app_yaml}")
        return False

    if not GLOBAL_CONFIG.exists():
        print(f"\n  [ERROR]  global user_config.yaml not found: {GLOBAL_CONFIG}")
        return False

    # -- Preflight: parse both YAMLs in memory before touching disk -----------
    # Prevents parse-related partial updates (e.g. one file unreadable before
    # any write begins). Runtime write failures after the first file succeeds
    # are not prevented -- restore from .bak files if that occurs.
    try:
        app_data_pre = preflight_yaml(app_yaml, "app")
        global_data_pre = preflight_yaml(GLOBAL_CONFIG, "global")
    except RuntimeError as exc:
        print(f"\n  [ERROR]  {exc}")
        return False

    # -- Update both YAMLs ---------------------
    merged_additional, new_from_yaml, collisions, app_valid = update_app_yaml(
        app_yaml, app_name, file_envs, dry_run, do_backup,
        preloaded_data=app_data_pre,
    )

    if not app_valid:
        print(f"\n  [ERROR]  Post-write validation failed for app YAML ({app_name})"
              f" -- skipping global YAML write. Check files and restore from backup.")
        return False

    global_valid = update_global_yaml(
        GLOBAL_CONFIG, app_name, merged_additional, dry_run, do_backup,
        preloaded_data=global_data_pre,
    )

    if not global_valid:
        print(f"\n  [ERROR]  Post-write validation failed for global YAML ({app_name})"
              f" -- check files and restore from backup.")
        return False

    # -- Sync env_variables file back ---------
    if bootstrap_mode:
        bootstrapped = {k: v for k, v in sorted(merged_additional.items())}
        print(f"\n>> Creating env_variables with {len(bootstrapped)} key(s) from YAML")
        if dry_run:
            _print_env_file_diff(
                env_file,
                before_envs={}, after_envs=bootstrapped,
                before_superseded={}, new_history={},
                clear_history=set(), cleared_and_colliding=set(),
            )
        write_env_file(
            env_file, bootstrapped,
            superseded={}, collisions={},
            clear_history=clear_history,
            dry_run=dry_run,
        )

    elif clear_history:
        # Rewrite env_variables with selected history lines omitted.
        # Must still use extended_envs (file + YAML-only keys) so the merge
        # rule -- YAML-only keys are written back to the file -- is honoured
        # even when --clear-history is the trigger for the rewrite.
        extended_envs = dict(file_envs)
        for k in new_from_yaml:
            if k not in IGNORED_KEYS:
                extended_envs[k] = merged_additional[k]

        print(f"\n>> Clearing superseded history for: {sorted(clear_history)}")
        # Keys that are both being cleared AND currently colliding: the new YAML
        # value is NOT re-added as a superseded entry this run. "Clear means
        # clear now." The collision is still resolved (file wins) -- it just
        # produces no history line for this run.
        cleared_and_colliding = clear_history & set(collisions)
        if cleared_and_colliding:
            print(f"   Note: {sorted(cleared_and_colliding)} also collide this run"
                  f" -- current YAML value not re-added to history (clear wins)")
        # Report per-key outcome so --all runs make it obvious which apps were affected.
        had_history    = sorted(k for k in clear_history if superseded.get(k))
        no_history     = sorted(k for k in clear_history if not superseded.get(k))
        if had_history:
            print(f"   [INFO]  History cleared for: {had_history}")
        if no_history:
            print(f"   [INFO]  No history to clear for: {no_history} (key(s) had none)")
        if new_from_yaml:
            print(f"   Also adding {len(new_from_yaml)} YAML-only key(s) back to file: {sorted(new_from_yaml)}")
        if dry_run:
            _print_env_file_diff(
                env_file,
                before_envs=file_envs, after_envs=extended_envs,
                before_superseded=superseded, new_history=collisions,
                clear_history=clear_history, cleared_and_colliding=cleared_and_colliding,
            )
        if do_backup and not dry_run:
            backup(env_file)
        write_env_file(
            env_file, extended_envs,
            superseded=superseded, collisions=collisions,
            clear_history=clear_history,
            dry_run=dry_run,
        )

    else:
        if collisions:
            print(f"\n>> Collision(s) detected - YAML value(s) will be commented in env_variables:")
            for k, v in collisions.items():
                file_disp = _display_value(k, file_envs[k])
                yaml_disp = _display_value(k, v)
                print(f"     {k}: file={file_disp!r}  yaml={yaml_disp!r}  -> file wins, yaml value commented")

        extended_envs = dict(file_envs)
        for k in new_from_yaml:
            if k not in IGNORED_KEYS:
                extended_envs[k] = merged_additional[k]

        if new_from_yaml or collisions:
            if new_from_yaml:
                print(f"\n>> Updating env_variables with {len(new_from_yaml)} new key(s) from YAML")
            if dry_run:
                _print_env_file_diff(
                    env_file,
                    before_envs=file_envs, after_envs=extended_envs,
                    before_superseded=superseded, new_history=collisions,
                    clear_history=set(), cleared_and_colliding=set(),
                )
            if do_backup and not dry_run:
                backup(env_file)
            write_env_file(
                env_file, extended_envs,
                superseded=superseded, collisions=collisions,
                clear_history=clear_history,
                dry_run=dry_run,
            )

    print(f"\n  [DONE]  {app_name} done{' (dry-run)' if dry_run else ''}.")
    return True


# --- Main ---

def main():
    parser = argparse.ArgumentParser(
        description="Sync env_variables file -> TrueNAS Scale app YAML configs"
    )

    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("app_name", nargs="?", help="App name, e.g. homepage")
    target.add_argument(
        "--all", action="store_true",
        help="Sync every app found in app_configs/",
    )

    parser.add_argument(
        "--dry-run", action="store_true", help="Print changes without writing files"
    )
    parser.add_argument(
        "--no-backup", action="store_true", help="Skip .bak backups"
    )
    parser.add_argument(
        "--clear-history", metavar="KEY", nargs="+",
        help="Remove superseded comment history for the given key(s) from env_variables "
             "(a normal sync still runs; this only controls which history lines are kept)",
    )

    args = parser.parse_args()
    dry_run = args.dry_run
    do_backup = not args.no_backup
    clear_history = set(args.clear_history) if args.clear_history else set()

    if args.all:
        if not APP_CONFIGS_ROOT.exists():
            sys.exit(f"ERROR: app_configs root not found: {APP_CONFIGS_ROOT}")

        app_dirs = sorted(
            d for d in APP_CONFIGS_ROOT.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        )
        if not app_dirs:
            sys.exit(f"ERROR: no app directories found under {APP_CONFIGS_ROOT}")

        print(f"\nFound {len(app_dirs)} app(s): {[d.name for d in app_dirs]}")

        results = {}
        for app_dir in app_dirs:
            try:
                ok = sync_app(app_dir.name, dry_run, do_backup, clear_history)
            except Exception as exc:
                print(f"\n  [ERROR]  Unexpected error syncing {app_dir.name!r}: {exc}")
                ok = False
            results[app_dir.name] = ok

        print(f"\n{'=' * 60}")
        print(f"  Summary  {'[DRY-RUN]' if dry_run else ''}")
        print(f"{'=' * 60}")
        for name, ok in results.items():
            status = "[OK]   " if ok else "[FAIL] "
            print(f"  {status} {name}")

        failed = [n for n, ok in results.items() if not ok]
        if failed:
            print(f"\n  {len(failed)} app(s) failed.")
            sys.exit(1)

    else:
        ok = sync_app(args.app_name, dry_run, do_backup, clear_history)
        print(f"\n{'=' * 60}")
        if not ok:
            sys.exit(1)

    print(f"\n{'=' * 60}")
    print(f"  [DONE]  All done{' (dry-run, nothing written)' if dry_run else ''}.")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()