# hermes-node

Turn any new device into a Hermes node that syncs itself from the canonical
private skills/config repos and auto-enrolls with your existing Hermes mesh
over Tailscale. **This repo holds install code only — no secrets, no skills
content, no config content ever lives here.**

## One-liner

Windows (PowerShell, run as your normal user — no elevation required):

```powershell
irm https://raw.githubusercontent.com/uuddhay/hermes-node/main/install.ps1 | iex
```

macOS / Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/uuddhay/hermes-node/main/install.sh | bash
```

Dry run first (recommended) — prints every action, changes nothing:

```powershell
$env:HERMES_NODE_DRY_RUN = "1"; irm https://raw.githubusercontent.com/uuddhay/hermes-node/main/install.ps1 | iex
```
```bash
HERMES_NODE_DRY_RUN=1 curl -fsSL https://raw.githubusercontent.com/uuddhay/hermes-node/main/install.sh | bash
```

Re-running the same command on an already-bootstrapped device is safe
(idempotent — every step checks current state before acting).

## What it does, in order

1. Verify/install Python 3.11+ and `uv`.
2. Verify/install Tailscale (does NOT log you in — that's your step).
3. `gh auth login` via **device code flow** if not already authenticated to
   GitHub (needed to clone the private `hermes-skills` and
   `hermes-config-bundle` repos).
4. Clone `hermes-skills` (canonical skills tree) and `hermes-config-bundle`
   (profiles, cron, SOULs, the 5 core-patch diffs) into a scratch dir.
5. Install Hermes as an editable git checkout via `uv`.
6. Apply the carried core patches deliberately (`scripts/apply_core_patches.py`
   — backs up every file it touches, refuses to overwrite unless the diff
   applies cleanly).
7. Restore the config bundle into `%LOCALAPPDATA%\hermes` / `~/.hermes`
   (profiles, cron/jobs.json, SOULs) without clobbering anything already
   present unless `--force`.
8. Run `skill_sync.py --apply` to fan the canonical skills tree into every
   profile.
9. `hermes gateway install` + `hermes gateway start`.
10. **Enroll**: run `tailscale up` (interactive login — your step), then
    `scripts/enroll_node.py`, which reads `tailscale status --json` (already
    an allow-list of your machines — no DHT, no gossip, no broadcast),
    `hermes peer add`s every Hermes node it finds there, and registers
    itself with the hub using a one-time enrollment token you paste once.

## Per-device steps only YOU can do

| Step | What you do |
|---|---|
| GitHub auth | The installer prints a device code + URL; open it, approve. |
| Tailscale | The installer runs `tailscale up`; approve the login in your browser, and approve the new device in the Tailscale admin console if approval is required on your tailnet. |
| Enrollment token | On the **hub** (your main machine), run `python hub-scripts/generate_enrollment_token.py`. It prints a one-time token. Paste that token when the new device's installer asks for it. |
| Secrets | Never typed into the public repo or this README. The new node fetches model API keys from the hub over the authenticated Tailscale link at enrollment time (`hub-scripts/accept_enrollment.py`, run manually by you on the hub), or you paste them once into a masked local prompt if you choose not to run the hub-side accept step. |

## Hub-side scripts (run these on your existing/main machine only)

- `hub-scripts/generate_enrollment_token.py` — mints a one-time, TTL-limited
  enrollment token, stored locally, printed for you to relay to the new
  device.
- `hub-scripts/accept_enrollment.py` — a standalone listener (NOT the
  gateway, NOT the live api_server) you run manually on the hub while
  enrolling a device. Validates the token, hands back hub peer info + (with
  your explicit y/N confirmation) the model API keys the new node needs, and
  runs `hermes peer add` locally to register the new node as the hub's peer.

## Explicitly out of scope

- No DHT, gossip, or broadcast discovery — Tailscale's ACL'd tailnet IS the
  allow-list.
- Never uses built-in `hermes sync` (Portal-only).
- Never touches money-path code, broker config, or order-placing profiles.
- Never writes a secret into this repo, a commit, or a log line.
