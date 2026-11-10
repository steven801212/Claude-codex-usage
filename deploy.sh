#!/usr/bin/env bash
# Usage: ./deploy.sh user@nas-ip   (copies to ~/token-panel and rebuilds)
set -euo pipefail
TARGET="${1:?用法: ./deploy.sh 帳號@NAS_IP}"
REMOTE_DIR="${REMOTE_DIR:-token-panel}"
cd "$(dirname "$0")"
ssh "$TARGET" "mkdir -p ~/$REMOTE_DIR/data"
# scp -r (rsync isn't always present on NAS); never touches ~/token-panel/data
scp -r app requirements.txt Dockerfile docker-entrypoint.sh docker-compose.yml .dockerignore "$TARGET:~/$REMOTE_DIR/"
ssh "$TARGET" "cd ~/$REMOTE_DIR && (docker compose version >/dev/null 2>&1 && docker compose up -d --build || docker-compose up -d --build)"
echo "部署完成：http://${TARGET#*@}:8787"
