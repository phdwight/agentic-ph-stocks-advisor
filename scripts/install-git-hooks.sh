#!/usr/bin/env bash
# One-time installer: symlink the tracked hooks into .git/hooks/.
#
# Usage:  ./scripts/install-git-hooks.sh
#
# Re-running is safe; existing symlinks pointing at the tracked scripts are
# replaced. A pre-existing non-symlink hook is preserved by renaming it to
# <name>.local so the user's customisations are never lost.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOKS_DIR="$REPO_ROOT/.git/hooks"
SCRIPTS_DIR="$REPO_ROOT/scripts"

mkdir -p "$HOOKS_DIR"

install_hook() {
  local name="$1"
  local source="$SCRIPTS_DIR/$name"
  local target="$HOOKS_DIR/$name"

  if [ ! -f "$source" ]; then
    echo "✗ $source does not exist; skipping."
    return
  fi

  chmod +x "$source"

  if [ -e "$target" ] && [ ! -L "$target" ]; then
    local backup="${target}.local"
    echo "▸ Existing $name hook is not a symlink — backing up to $backup"
    mv "$target" "$backup"
  fi

  ln -snf "$source" "$target"
  echo "✔ Installed hook: $name → ${source#$REPO_ROOT/}"
}

install_hook pre-push

echo
echo "Done. Disable temporarily with:  SKIP_MEMORY_UPDATE=1 git push"
