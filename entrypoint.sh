#!/bin/bash
set -e

GIT_TOKEN=$(cat /run/secrets/cardea_github_token)
REPO_DIR=/opt/cardea-src

# Clone or pull latest
if [ -d "$REPO_DIR/.git" ]; then
  cd "$REPO_DIR" && git pull --ff-only
else
  git clone "https://x-access-token:${GIT_TOKEN}@github.com/fcvr1010/cardea.git" "$REPO_DIR"
fi

cd "$REPO_DIR"
ln -sf /opt/cardea/config.toml config.toml
uv sync --frozen --no-dev

# Start Cardea (foreground)
exec uv run cardea
