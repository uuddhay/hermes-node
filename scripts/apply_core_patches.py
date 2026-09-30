#!/usr/bin/env python3
"""
apply_core_patches.py — template. The real copy ships inside the PRIVATE
hermes-config-bundle repo (scripts/apply_core_patches.py) alongside the
five patch diffs it applies. This public copy exists so the installer's
behaviour is inspectable and testable without the private repo.

Applies a set of unified diffs against a fresh hermes-agent git checkout,
for files where no config knob exists upstream. Never blind-overwrites:
  - backs up every target file to <file>.bak-<timestamp> before touching it
  - refuses (exits non-zero, changes nothing for that file) if the diff
    does not apply cleanly (upstream has diverged — needs a human to
    re-review whether the patch is still needed, per the maintainer's
    per-patch review rule)
  - is idempotent: if the file already matches the patched content, skips

Expected layout in the config bundle:
  patches/
    agent-insights.patch
    agent-usage_pricing.patch
    gateway-kanban_watchers_common.patch
    gateway-kanban_watchers_dispatcher.patch
    gateway-kanban_watchers_notifier.patch
  patches/manifest.json   # [{"file": "agent/insights.py", "patch": "agent-insights.patch", "reason": "..."}]
"""
import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hermes-agent-dir", required=True)
    ap.add_argument("--patches-dir", default=None, help="defaults to <this script's dir>/../patches")
    ap.add_argument("--backup", action="store_true", default=True)
    ap.add_argument("--dry-run", action="store_true", default=None)
    args = ap.parse_args()

    dry_run = args.dry_run if args.dry_run is not None else (__import__("os").environ.get("HERMES_NODE_DRY_RUN") == "1")

    hermes_dir = Path(args.hermes_agent_dir)
    patches_dir = Path(args.patches_dir) if args.patches_dir else Path(__file__).resolve().parent.parent / "patches"
    manifest_path = patches_dir / "manifest.json"

    if not manifest_path.exists():
        print(f"no manifest at {manifest_path} — nothing to apply (config bundle not present or empty)")
        return 0

    manifest = json.loads(manifest_path.read_text())
    print(f"applying {len(manifest)} core patch(es) to {hermes_dir}  (dry-run={dry_run})")

    failures = []
    for entry in manifest:
        target = hermes_dir / entry["file"]
        patch_file = patches_dir / entry["patch"]
        reason = entry.get("reason", "")
        print(f"\n-- {entry['file']}  ({reason})")

        if not target.exists():
            print(f"   SKIP: target file does not exist (upstream removed/renamed it — review manually)")
            failures.append(entry["file"])
            continue
        if not patch_file.exists():
            print(f"   SKIP: patch file missing: {patch_file}")
            failures.append(entry["file"])
            continue

        # Check-apply first (dry) to see if it's clean or already applied
        check = subprocess.run(
            ["git", "apply", "--check", "--directory", str(hermes_dir), str(patch_file)],
            capture_output=True, text=True,
        )
        if check.returncode != 0:
            reverse_check = subprocess.run(
                ["git", "apply", "--check", "--reverse", "--directory", str(hermes_dir), str(patch_file)],
                capture_output=True, text=True,
            )
            if reverse_check.returncode == 0:
                print("   already applied, skipping")
                continue
            print(f"   REFUSED: patch does not apply cleanly against current upstream.")
            print(f"   This likely means upstream has changed this file since the patch was")
            print(f"   captured — review whether the patch is still needed before forcing it.")
            print(f"   git apply --check said:\n{check.stderr}")
            failures.append(entry["file"])
            continue

        if dry_run:
            print(f"   [DRY-RUN] would: backup {target} -> {target}.bak-<timestamp>, then git apply {patch_file}")
            continue

        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = target.with_suffix(target.suffix + f".bak-{ts}")
        backup.write_bytes(target.read_bytes())
        print(f"   backed up -> {backup}")

        apply = subprocess.run(
            ["git", "apply", "--directory", str(hermes_dir), str(patch_file)],
            capture_output=True, text=True,
        )
        if apply.returncode != 0:
            print(f"   APPLY FAILED after check passed (race?): {apply.stderr}")
            failures.append(entry["file"])
            continue
        print("   applied")

    if failures:
        print(f"\n{len(failures)} patch(es) need manual review: {failures}")
        return 1
    print("\nall patches applied (or already present)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
