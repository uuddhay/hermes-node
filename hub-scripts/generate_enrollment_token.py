#!/usr/bin/env python3
"""
generate_enrollment_token.py — run on the HUB (your main machine) only.

Mints a one-time, TTL-limited enrollment token and prints it for you to
relay (voice, Signal, whatever out-of-band channel you trust) to the new
device's operator (you, on the new device). The token is stored locally
in a small SQLite/JSON state file under the hub's Hermes cache dir — never
committed, never in this repo.

The token itself grants nothing by itself: it only lets a node complete a
POST to accept_enrollment.py (which you must also be running, manually,
on the hub, while enrolling) within its TTL. There is no standing listener
opened by this script and no port is bound by generate — only by
accept_enrollment.py, and only while you run it.
"""
import argparse
import datetime
import json
import os
import secrets
from pathlib import Path

STATE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "hermes" / "cache" / "hermes-node" \
    if os.name == "nt" else Path.home() / ".hermes" / "cache" / "hermes-node"
TOKENS_FILE = STATE_DIR / "enrollment_tokens.json"


def load_tokens():
    if TOKENS_FILE.exists():
        return json.loads(TOKENS_FILE.read_text())
    return {}


def save_tokens(tokens):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    TOKENS_FILE.write_text(json.dumps(tokens, indent=2))
    try:
        os.chmod(TOKENS_FILE, 0o600)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ttl-minutes", type=int, default=30)
    ap.add_argument("--label", default="", help="optional note, e.g. 'new laptop'")
    args = ap.parse_args()

    token = secrets.token_urlsafe(24)
    expires = (datetime.datetime.utcnow() + datetime.timedelta(minutes=args.ttl_minutes)).isoformat() + "Z"

    tokens = load_tokens()
    tokens[token] = {
        "created": datetime.datetime.utcnow().isoformat() + "Z",
        "expires": expires,
        "label": args.label,
        "used": False,
    }
    save_tokens(tokens)

    print("One-time enrollment token (paste this on the NEW device when its installer asks):\n")
    print(f"    {token}\n")
    print(f"Expires: {expires}  (TTL {args.ttl_minutes} min)")
    print(f"\nNow run hub-scripts/accept_enrollment.py on THIS machine and leave it running")
    print(f"until the new device completes enrollment — it prints the exact address the")
    print(f"new node needs to contact.")


if __name__ == "__main__":
    main()
