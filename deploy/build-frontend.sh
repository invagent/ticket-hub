#!/usr/bin/env bash
# Build the frontend inside a throwaway node container (host needs no node)
# and publish the static dist to the nginx-served path.
#
# Usage: deploy/build-frontend.sh [OUT_DIR]
#   OUT_DIR defaults to /data/hub-issue/frontend-dist
#
# Base paths match the nginx /hub-issue/ mount (deploy/nginx/hub-issue.conf).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-/data/hub-issue/frontend-dist}"

echo "==> building frontend (VITE_PUBLIC_BASE=/hub-issue/) ..."
docker run --rm \
  -v "$REPO_ROOT/frontend:/app" \
  -w /app \
  node:20-alpine sh -c '
    npm ci &&
    VITE_PUBLIC_BASE=/hub-issue/ VITE_API_BASE=/hub-issue npm run build
  '

echo "==> publishing dist -> $OUT"
mkdir -p "$OUT"
rm -rf "${OUT:?}"/*
cp -r "$REPO_ROOT/frontend/dist/." "$OUT/"
echo "==> done. reload nginx if the path is new: nginx -s reload"
