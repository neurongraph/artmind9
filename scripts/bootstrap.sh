#!/usr/bin/env bash
# One-time machine setup for artmind (spec docs/superpowers/specs/2026-10-02-vault-new-design.md §1).
#
# Idempotent: every step checks first and skips what is already done, so it is
# safe to re-run after a failure or on an already-set-up machine. macOS only.
#
#   bash scripts/bootstrap.sh            # from a checkout
#   ARTMIND_SRC=~/code bash bootstrap.sh # clone the repos somewhere else
#
# Then: edit ~/.artmind/config.env, and run `artmind vault new <name>`.
set -euo pipefail

SRC="${ARTMIND_SRC:-$HOME/projects}"
ORG="neurongraph"

say()  { printf '\033[1m==> %s\033[0m\n' "$*"; }
skip() { printf '    %s (already done)\n' "$*"; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "bootstrap.sh supports macOS only."
command -v brew >/dev/null || die "Homebrew is required: https://brew.sh"

say "Homebrew packages"
for pkg in uv just gh node colima docker; do
    if brew list --formula "$pkg" >/dev/null 2>&1; then skip "$pkg"; else brew install "$pkg"; fi
done
if [ -d /Applications/Obsidian.app ]; then skip "Obsidian"; else brew install --cask obsidian; fi

say "GitHub CLI login"
if gh auth status >/dev/null 2>&1; then skip "gh auth ($(gh api user --jq .login))"; else gh auth login; fi
gh auth setup-git   # git push over https uses gh's credentials; idempotent

say "git identity"
for key in user.name user.email; do
    if [ -n "$(git config --global --get "$key" || true)" ]; then
        skip "$key"
    else
        printf '    warning: git %s is not set -- artmind vault new needs it: git config --global %s <value>\n' "$key" "$key"
    fi
done

say "colima (Docker runtime for neo4j-manager)"
if colima status >/dev/null 2>&1; then skip "colima running"; else colima start; fi

say "Source checkouts in $SRC"
mkdir -p "$SRC"
# clone <repo> <dir>...: prints the checkout's path on stdout (everything else
# goes to stderr); an existing checkout under any candidate name counts.
clone() {
    local repo="$1"; shift
    for dir in "$@"; do
        if [ -d "$SRC/$dir/.git" ]; then skip "$repo ($SRC/$dir)" >&2; echo "$SRC/$dir"; return; fi
    done
    gh repo clone "$ORG/$repo" "$SRC/$1" >&2
    echo "$SRC/$1"
}
ARTMIND_DIR="$(clone artmind9 artmind9)"
NEO4J_MANAGER_DIR="$(clone neo4j-manager neo4j-manager neo4j_manager)"

say "Install neo4j-manager"
(cd "$NEO4J_MANAGER_DIR" && just install)

say "Install artmind"
(cd "$ARTMIND_DIR" && just dev-install)

say "Machine config (~/.artmind/config.env)"
"$(uv tool dir)/artmind9/bin/python" -c '
from artmind.setup import ensure_machine_config
r = ensure_machine_config()
print("   ", r["action"] + ":", r["path"])
'

cat <<EOF

Machine ready. Next:

  1. Edit ~/.artmind/config.env    # LLM provider, model, API key, embeddings -- shared by every vault
  2. artmind vault new <name>      # a new vault: Neo4j, private GitHub repo $(gh api user --jq .login 2>/dev/null || echo '<you>')/<name>, Obsidian plugins
     (or: artmind vault new <name> --join   on a second machine, to join an existing vault)
EOF
