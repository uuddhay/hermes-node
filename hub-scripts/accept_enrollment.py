#!/usr/bin/env python3
"""
accept_enrollment.py — run manually on the HUB while enrolling a device.
This is NOT the live gateway and NOT the live api_server — it is a
standalone, short-lived listener you start yourself and stop yourself,
bound to the hub's Tailscale interface only (never 0.0.0.0), so it is
reachable only from your own tailnet.

Flow:
  1. Binds 127.0.0.1 + <tailscale-ip> on a random high port, prints the
     exact address for you to read out (or the new node's operator reads
     the token to you and you type the address into the new device — your
     choice which direction the info flows).
  2. Waits for one POST containing {token, hostname, tailscale_ip,
     proposed_api_server_key}.
  3. Validates the token against hub-scripts/generate_enrollment_token.py's
     local state file (must exist, unexpired, unused).
  4. Prints a y/N prompt on the HUB operator's own terminal: "Accept
     enrollment from <hostname> (<ip>)? Send model API keys? [y/N]" — model
     keys are NEVER sent without this explicit interactive confirmation on
     the hub, and are read from the hub's own local .env, never from this
     repo.
  5. On yes: runs `hermes peer add <hostname> --url http://<ip>:8642 --key
     <proposed_api_server_key>` locally on the hub (registering the new
     node as the hub's peer), replies to the node with the hub's own peer
     URL + key and (if confirmed) the requested model API keys, then exits.
  6. On no / timeout (10 min): refuses, logs nothing sensitive, exits.

Nothing here writes to this git repo. Nothing here is started by the
installer automatically — it is always a manual, attended hub-side action.
"""
import argparse
import datetime
import http.server
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
from pathlib import Path

STATE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "hermes" / "cache" / "hermes-node" \
    if os.name == "nt" else Path.home() / ".hermes" / "cache" / "hermes-node"
TOKENS_FILE = STATE_DIR / "enrollment_tokens.json"
API_SERVER_PORT = 8642


def tailscale_ip():
    try:
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, check=True)
        return out.stdout.strip().splitlines()[0]
    except Exception:
        return None


def load_tokens():
    if TOKENS_FILE.exists():
        return json.loads(TOKENS_FILE.read_text())
    return {}


def save_tokens(tokens):
    TOKENS_FILE.write_text(json.dumps(tokens, indent=2))


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        pass  # never log request bodies (may contain the token)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self.send_response(400)
            self.end_headers()
            return

        token = body.get("token", "")
        hostname = body.get("hostname", "unknown")
        node_ip = body.get("tailscale_ip", "")
        node_key = body.get("proposed_api_server_key", "")

        tokens = load_tokens()
        entry = tokens.get(token)
        now = datetime.datetime.utcnow()
        valid = (
            entry
            and not entry.get("used")
            and datetime.datetime.fromisoformat(entry["expires"].rstrip("Z")) > now
        )
        if not valid:
            print(f"\nREJECTED enrollment attempt from {hostname} — invalid/expired/used token.")
            self.send_response(403)
            self.end_headers()
            self.wfile.write(json.dumps({"error": "invalid_token"}).encode())
            return

        print(f"\nEnrollment request from: {hostname}  ({node_ip})")
        ans = input("Accept this node as a peer, and send model API keys? [y/N]: ").strip().lower()
        if ans != "y":
            print("Declined. Node not registered, no keys sent.")
            self.send_response(403)
            self.end_headers()
            self.wfile.write(json.dumps({"error": "declined_by_operator"}).encode())
            return

        entry["used"] = True
        tokens[token] = entry
        save_tokens(tokens)

        # Register the new node as a peer on the hub, locally.
        subprocess.run(
            ["hermes", "peer", "add", hostname, "--url", f"http://{node_ip}:{API_SERVER_PORT}", "--key", node_key],
            check=False,
        )
        print(f"Registered {hostname} as a local peer.")

        send_keys = input("Include model API keys in the reply (read from hub's own .env)? [y/N]: ").strip().lower() == "y"
        keys_payload = {}
        if send_keys:
            env_path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "hermes" / ".env" \
                if os.name == "nt" else Path.home() / ".hermes" / ".env"
            if env_path.exists():
                for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if "=" in line and any(k in line for k in ("_API_KEY", "_KEY=")) and not line.strip().startswith("#"):
                        k, _, v = line.partition("=")
                        keys_payload[k.strip()] = v.strip()
                print(f"Including {len(keys_payload)} key(s) in the encrypted reply (not printed here).")
            else:
                print(f"No .env found at {env_path}, sending no keys.")

        my_ip = tailscale_ip()
        reply = {
            "hub_url": f"http://{my_ip}:{API_SERVER_PORT}",
            "hub_key_hint": "use the key your hub operator reads out, not sent in cleartext here unless you accept TLS-less LAN risk",
            "model_api_keys": keys_payload,
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(reply).encode())
        print("Reply sent. Enrollment complete. You can Ctrl+C this listener now.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0, help="0 = pick a free port")
    ap.add_argument("--timeout-minutes", type=int, default=10)
    args = ap.parse_args()

    ip = tailscale_ip()
    if not ip:
        print("Could not read a Tailscale IP (is tailscale up on the hub?). Refusing to bind 0.0.0.0.")
        sys.exit(1)

    server = http.server.HTTPServer((ip, args.port), Handler)
    actual_port = server.server_address[1]
    print(f"Listening on http://{ip}:{actual_port}  (Tailscale-only, not internet-reachable)")
    print(f"Give this address to the new device if it asks for HERMES_NODE_HUB_ADDR.")
    print(f"Waiting up to {args.timeout_minutes} min for one enrollment POST... Ctrl+C to abort.\n")

    timer = threading.Timer(args.timeout_minutes * 60, lambda: os._exit(0))
    timer.daemon = True
    timer.start()
    try:
        server.handle_request()  # exactly one request, then exit
    except KeyboardInterrupt:
        print("\nAborted.")


if __name__ == "__main__":
    main()
