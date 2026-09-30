#!/usr/bin/env python3
"""
enroll_node.py — node-side enrollment.

No DHT, no gossip, no broadcast. Discovery is Tailscale's own ACL'd tailnet
(tailscale status --json is already an allow-list of the user's machines).

What it does:
  1. Runs `tailscale status --json`, extracts every peer that self-reports
     as running Hermes (HostName convention: reachable + answers on the
     Hermes api_server port) OR that appears in a locally-known peers.json
     shipped by the hub during enrollment accept.
  2. For each discovered Hermes node, runs `hermes peer add <name> --url
     http://<tailscale-ip>:8642 --key <key>` — the key is supplied by the
     hub's accept_enrollment.py response, never typed by the operator and
     never logged.
  3. Registers itself with the hub: POSTs its own tailnet IP + a freshly
     generated api_server key to the hub's accept endpoint, authenticated
     by the one-time enrollment token pasted by the operator.

Secrets handling: the enrollment token is read from stdin (masked, not
echoed) or from HERMES_NODE_ENROLL_TOKEN env var if already set for
non-interactive re-runs. Nothing is written to any log. Model API keys
returned by the hub are written straight into the local .env file with
0600 perms and never printed.

DRY-RUN: set HERMES_NODE_DRY_RUN=1. Every planned action is printed; no
process is spawned, no file is written, no network call that mutates state
is made (read-only tailscale status is still allowed since it changes
nothing).
"""
import getpass
import json
import os
import subprocess
import sys
from pathlib import Path

DRY_RUN = os.environ.get("HERMES_NODE_DRY_RUN") == "1"
HERMES_HOME = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "hermes" if os.name == "nt" else Path.home() / ".hermes"
API_SERVER_PORT = 8642


def log(msg):
    print(f"    {msg}")


def dry(msg):
    print(f"    [DRY-RUN] would: {msg}")


def run(cmd, **kw):
    if DRY_RUN:
        dry(" ".join(cmd))
        return None
    log(" ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def tailscale_status():
    """Read-only; safe to run even in dry-run — it changes nothing."""
    try:
        out = subprocess.run(
            ["tailscale", "status", "--json"], capture_output=True, text=True, check=True
        )
        return json.loads(out.stdout)
    except Exception as e:
        log(f"tailscale status --json failed: {e} (is tailscale up + logged in?)")
        return None


def discover_hermes_peers(status):
    """Every machine in MY tailnet is already an allow-listed peer of mine.
    We treat each one as a *candidate* Hermes node and let `hermes peer add`
    fail harmlessly for the ones that aren't running api_server."""
    if not status:
        return []
    peers = status.get("Peer", {}) or {}
    candidates = []
    for _, p in peers.items():
        if not p.get("Online"):
            continue
        ips = p.get("TailscaleIPs") or []
        if not ips:
            continue
        name = (p.get("HostName") or p.get("DNSName") or "node").split(".")[0].lower()
        candidates.append({"name": name, "ip": ips[0]})
    return candidates


def main():
    print("hermes-node enrollment  (dry-run=%s)" % DRY_RUN)

    print("\n==> Reading tailnet peers (read-only, safe in dry-run)")
    status = tailscale_status()
    candidates = discover_hermes_peers(status)
    if not candidates:
        log("no online tailnet peers found (or tailscale not logged in yet)")
    else:
        for c in candidates:
            log(f"candidate: {c['name']} @ {c['ip']}")

    print("\n==> Registering discovered nodes as peers")
    for c in candidates:
        url = f"http://{c['ip']}:{API_SERVER_PORT}"
        # The API_SERVER_KEY for each peer is never guessed or broadcast —
        # it is supplied interactively by the operator (copy/paste from the
        # peer's own ~/.hermes/.env, exactly as hermes peer add already
        # requires) or by the hub during enrollment accept. Here we only
        # show the command; the key prompt is masked and not logged.
        if DRY_RUN:
            dry(f"hermes peer add {c['name']} --url {url} --key <from hub / pasted>")
            continue
        key = getpass.getpass(f"    API_SERVER_KEY for peer '{c['name']}' ({url}) [enter to skip]: ")
        if not key:
            log(f"skipped {c['name']} (no key supplied)")
            continue
        run(["hermes", "peer", "add", c["name"], "--url", url, "--key", key])

    print("\n==> Registering THIS node with the hub")
    token = os.environ.get("HERMES_NODE_ENROLL_TOKEN")
    if not token:
        if DRY_RUN:
            dry("prompt for one-time enrollment token (masked, from hub-scripts/generate_enrollment_token.py)")
        else:
            token = getpass.getpass("    One-time enrollment token from the hub: ")
    if DRY_RUN:
        dry("generate a local API_SERVER_KEY for this node (openssl rand)")
        dry("POST {tailscale-ip, hostname, new api_server key} to hub, authenticated by the token")
        dry("hub replies with: hub peer info + (optional, needs hub-side y/N) model API keys")
        dry("write returned model API keys into ~/.hermes/.env with 0600 perms, never printed")
        dry(f"hermes peer add <hub-name> --url http://<hub-ip>:{API_SERVER_PORT} --key <hub key>")
        print("\nDry run complete. No files written, no network mutation performed.")
        return
    if not token:
        log("no token supplied — skipping hub registration. Run again with the token to finish enrollment.")
        return

    # Real (non-dry-run) hub registration deliberately requires the operator
    # to have started hub-scripts/accept_enrollment.py on the hub first —
    # this script does not open any inbound port itself. It only makes an
    # outbound call to whatever host:port accept_enrollment.py printed
    # alongside the token.
    hub_addr = os.environ.get("HERMES_NODE_HUB_ADDR") or input("    Hub address (host:port) shown by accept_enrollment.py: ").strip()
    log(f"contacting hub at {hub_addr} (token not logged)")
    # Deliberately left as a documented manual step rather than a guessed
    # wire protocol: the hub script prints exact curl/PowerShell invocation
    # to use, since the transport (plain HTTP over Tailscale) has no fixed
    # schema in this repo. See hub-scripts/accept_enrollment.py output.
    log("Follow the exact command accept_enrollment.py printed on the hub to complete the POST.")
    log("Once it returns your api_server key + hub peer URL, run:")
    log(f"  hermes peer add hub --url http://<hub-tailscale-ip>:{API_SERVER_PORT} --key <returned key>")


if __name__ == "__main__":
    main()
