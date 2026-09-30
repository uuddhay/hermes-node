#!/usr/bin/env bash
# hermes-node installer (macOS / Linux)
# curl -fsSL https://raw.githubusercontent.com/uuddhay/hermes-node/main/install.sh | bash
#
# Env vars:
#   HERMES_NODE_DRY_RUN=1        print every action, change nothing
#   HERMES_NODE_SKILLS_REPO      default https://github.com/uuddhay/hermes-skills.git
#   HERMES_NODE_CONFIG_REPO      default https://github.com/uuddhay/hermes-config-bundle.git
#   HERMES_NODE_FORCE=1          overwrite existing config bundle files instead of skipping them

set -euo pipefail

DRY_RUN="${HERMES_NODE_DRY_RUN:-0}"
FORCE="${HERMES_NODE_FORCE:-0}"
SKILLS_REPO="${HERMES_NODE_SKILLS_REPO:-https://github.com/uuddhay/hermes-skills.git}"
CONFIG_REPO="${HERMES_NODE_CONFIG_REPO:-https://github.com/uuddhay/hermes-config-bundle.git}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SCRATCH="${TMPDIR:-/tmp}/hermes-node-bootstrap"
NODE_REPO="https://github.com/uuddhay/hermes-node.git"

step()  { printf "\n==> %s\n" "$1"; }
info()  { printf "    %s\n" "$1"; }
act() {
  # act "description" cmd args...
  local desc="$1"; shift
  if [ "$DRY_RUN" = "1" ]; then
    printf "    [DRY-RUN] would: %s\n" "$desc"
  else
    printf "    %s\n" "$desc"
    "$@"
  fi
}

echo "hermes-node installer  (dry-run=$DRY_RUN)"

# 1. Python 3.11+
step "Checking Python 3.11+"
PY_OK=0
if command -v python3 >/dev/null 2>&1; then
  PYVER=$(python3 --version 2>&1)
  info "found: $PYVER"
  MAJOR=$(python3 -c 'import sys; print(sys.version_info[0])')
  MINOR=$(python3 -c 'import sys; print(sys.version_info[1])')
  if [ "$MAJOR" -gt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -ge 11 ]; }; then PY_OK=1; fi
else
  info "python3 not found"
fi
if [ "$PY_OK" -ne 1 ]; then
  if [ "$(uname)" = "Darwin" ]; then
    act "brew install python@3.12" brew install python@3.12
  else
    act "apt-get install -y python3.12 (Debian/Ubuntu path)" bash -c 'sudo apt-get update && sudo apt-get install -y python3.12 python3.12-venv'
  fi
else
  info "OK, skipping install"
fi

# 2. uv
step "Checking uv"
if command -v uv >/dev/null 2>&1; then
  info "found: $(uv --version)"
else
  act "install uv via astral.sh installer" bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
fi

# 3. Tailscale (install only — login is the user's manual step)
step "Checking Tailscale"
if command -v tailscale >/dev/null 2>&1; then
  info "already installed"
else
  if [ "$(uname)" = "Darwin" ]; then
    act "brew install tailscale" brew install tailscale
  else
    act "install via tailscale.com/install.sh" bash -c 'curl -fsSL https://tailscale.com/install.sh | sh'
  fi
fi

# 4. GitHub device-code auth
step "Checking GitHub auth (gh CLI)"
if ! command -v gh >/dev/null 2>&1; then
  if [ "$(uname)" = "Darwin" ]; then
    act "brew install gh" brew install gh
  else
    act "install gh via apt" bash -c 'sudo apt-get update && sudo apt-get install -y gh'
  fi
fi
if gh auth status >/dev/null 2>&1; then
  info "already logged in"
else
  act "gh auth login --git-protocol https (device code flow)" gh auth login --git-protocol https --hostname github.com
fi

# 5. Clone private repos
step "Cloning private repos (skills + config bundle)"
act "mkdir -p $SCRATCH" mkdir -p "$SCRATCH"
SKILLS_DIR="$SCRATCH/hermes-skills"
CONFIG_DIR="$SCRATCH/hermes-config-bundle"
if [ ! -d "$SKILLS_DIR" ]; then
  act "git clone $SKILLS_REPO -> $SKILLS_DIR" git clone --depth 1 "$SKILLS_REPO" "$SKILLS_DIR"
else
  info "$SKILLS_DIR already present, pulling"
  act "git -C $SKILLS_DIR pull --ff-only" git -C "$SKILLS_DIR" pull --ff-only
fi
if [ ! -d "$CONFIG_DIR" ]; then
  act "git clone $CONFIG_REPO -> $CONFIG_DIR" git clone --depth 1 "$CONFIG_REPO" "$CONFIG_DIR"
else
  info "$CONFIG_DIR already present, pulling"
  act "git -C $CONFIG_DIR pull --ff-only" git -C "$CONFIG_DIR" pull --ff-only
fi

# 6. Install Hermes (editable git checkout via uv)
step "Installing Hermes (editable git checkout)"
HERMES_AGENT_DIR="$HERMES_HOME/hermes-agent"
if [ ! -d "$HERMES_AGENT_DIR" ]; then
  act "git clone https://github.com/NousResearch/hermes-agent.git -> $HERMES_AGENT_DIR" \
    git clone https://github.com/NousResearch/hermes-agent.git "$HERMES_AGENT_DIR"
  act "uv venv + uv pip install -e in $HERMES_AGENT_DIR" bash -c "cd '$HERMES_AGENT_DIR' && uv venv && uv pip install -e ."
else
  info "$HERMES_AGENT_DIR already exists, skipping clone+install"
fi

# 7. Apply carried core patches deliberately
step "Applying core patches from config bundle"
PATCH_SCRIPT="$CONFIG_DIR/scripts/apply_core_patches.py"
if [ -f "$PATCH_SCRIPT" ]; then
  act "python3 $PATCH_SCRIPT --hermes-agent-dir $HERMES_AGENT_DIR --backup" \
    python3 "$PATCH_SCRIPT" --hermes-agent-dir "$HERMES_AGENT_DIR" --backup
else
  info "no apply_core_patches.py in config bundle, skipping"
fi

# 8. Restore config bundle without clobbering
step "Restoring config bundle into $HERMES_HOME"
RESTORE_SCRIPT="$CONFIG_DIR/scripts/restore_config_bundle.py"
if [ -f "$RESTORE_SCRIPT" ]; then
  FORCE_ARGS=()
  [ "$FORCE" = "1" ] && FORCE_ARGS=(--force)
  act "python3 $RESTORE_SCRIPT --src $CONFIG_DIR --dest $HERMES_HOME" \
    python3 "$RESTORE_SCRIPT" --src "$CONFIG_DIR" --dest "$HERMES_HOME" "${FORCE_ARGS[@]}"
else
  info "no restore_config_bundle.py in config bundle, skipping"
fi

# 9. Skill sync fan-out
step "Running skill_sync.py --apply"
SYNC_SCRIPT="$HERMES_HOME/scripts/skill_sync.py"
if [ -f "$SYNC_SCRIPT" ]; then
  act "python3 $SYNC_SCRIPT --apply" python3 "$SYNC_SCRIPT" --apply
else
  info "skill_sync.py not present yet at $SYNC_SCRIPT, skipping"
fi

# 10. Gateway install + start
step "Installing and starting the Hermes gateway"
act "hermes gateway install" hermes gateway install
act "hermes gateway start" hermes gateway start

# 11. Enroll
step "Enrolling with your Hermes mesh"
info "You'll be asked to log in to Tailscale and to paste a one-time enrollment"
info "token generated on your hub by hub-scripts/generate_enrollment_token.py"
act "tailscale up" tailscale up
if [ ! -d "$SCRATCH/hermes-node" ]; then
  act "git clone $NODE_REPO -> $SCRATCH/hermes-node" git clone --depth 1 "$NODE_REPO" "$SCRATCH/hermes-node"
fi
ENROLL_SCRIPT="$SCRATCH/hermes-node/scripts/enroll_node.py"
if [ "$DRY_RUN" = "1" ]; then
  printf "    [DRY-RUN] would: python3 %s\n" "$ENROLL_SCRIPT"
else
  python3 "$ENROLL_SCRIPT"
fi

echo ""
echo "Done. Re-run this same command any time — every step is idempotent."
