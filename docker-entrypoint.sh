#!/bin/sh
set -e
mkdir -p "$HOME" "$CLAUDE_CONFIG_DIR" "$CODEX_HOME"
chmod 700 "$CLAUDE_CONFIG_DIR" "$CODEX_HOME" 2>/dev/null || true
# Make Codex store auth.json as a plain file (no OS keyring in a container)
if [ ! -f "$CODEX_HOME/config.toml" ]; then
  printf 'cli_auth_credentials_store = "file"\n' > "$CODEX_HOME/config.toml"
fi
exec "$@"
