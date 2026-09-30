#!/usr/bin/env python3
"""
restore_config_bundle.py — template (real copy ships in the PRIVATE
hermes-config-bundle repo). Restores profiles/, cron/jobs.json, SOULs and
config.yaml FRAGMENTS (never secrets — those live in a local .env prompt or
come from the hub at enrollment) from the config bundle into the live
Hermes install root.

Safety rules:
  - never overwrites an existing file unless --force is passed
  - never touches anything under profiles/*/.env or any *.env file —
    those are always operator/hub supplied, never bundle-shipped
  - refuses to write into profiles that aren't in the bundle's manifest
    (no accidental profile creation — profile roster is Uddhay's call,
    not an installer's)
  - dry-run prints the full diff of what would be copied/skipped
"""
import argparse
import filecmp
import json
import os
import shutil
import sys
from pathlib import Path

DISALLOWED_SUFFIXES = {".env"}


def should_skip_secret_path(path: Path) -> bool:
    return path.suffix in DISALLOWED_SUFFIXES or path.name == ".env"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="config bundle checkout dir")
    ap.add_argument("--dest", required=True, help="live Hermes install root, e.g. %LOCALAPPDATA%/hermes")
    ap.add_argument("--force", action="store_true", default=False)
    ap.add_argument("--dry-run", action="store_true", default=None)
    args = ap.parse_args()

    dry_run = args.dry_run if args.dry_run is not None else (os.environ.get("HERMES_NODE_DRY_RUN") == "1")

    src = Path(args.src)
    dest = Path(args.dest)
    manifest_path = src / "manifest.json"

    if not manifest_path.exists():
        print(f"no manifest.json at {manifest_path} — nothing to restore (config bundle empty or not cloned)")
        return 0

    manifest = json.loads(manifest_path.read_text())
    # manifest example:
    # {"restore_paths": ["cron/jobs.json", "profiles/coder/SOUL.md", ...]}
    paths = manifest.get("restore_paths", [])
    print(f"restoring {len(paths)} path(s) from {src} into {dest}  (force={args.force}, dry-run={dry_run})")

    copied, skipped, refused_secret = 0, 0, 0
    for rel in paths:
        rel_path = Path(rel)
        if should_skip_secret_path(rel_path):
            print(f"REFUSED (secret path never bundle-shipped): {rel}")
            refused_secret += 1
            continue
        s = src / rel_path
        d = dest / rel_path
        if not s.exists():
            print(f"SKIP (missing in bundle): {rel}")
            continue
        if d.exists() and not args.force:
            if s.is_file() and d.is_file() and filecmp.cmp(s, d, shallow=False):
                print(f"SKIP (identical, already present): {rel}")
            else:
                print(f"SKIP (exists, differs, --force not set): {rel}")
            skipped += 1
            continue
        if dry_run:
            print(f"[DRY-RUN] would copy: {rel}  ({s} -> {d})")
            copied += 1
            continue
        d.parent.mkdir(parents=True, exist_ok=True)
        if s.is_dir():
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)
        print(f"copied: {rel}")
        copied += 1

    print(f"\n{copied} copied/would-copy, {skipped} skipped (existing), {refused_secret} refused (secret path)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
