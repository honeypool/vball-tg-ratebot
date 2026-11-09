#!/bin/sh
# Update and restart the bot on the server:  ./deploy.sh
set -e
cd "$(dirname "$0")"
git pull --ff-only
mkdir -p data && chown 1000:1000 data 2>/dev/null || true
docker compose up -d --build
docker image prune -f >/dev/null
docker compose logs --tail 30 bot
