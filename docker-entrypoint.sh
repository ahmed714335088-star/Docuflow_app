#!/bin/sh
set -eu

APP_UID="${APP_UID:-1000}"
APP_GID="${APP_GID:-1000}"
mkdir -p /app/data/outputs /app/data/work /app/cache/huggingface

if [ "$(id -u)" -eq 0 ]; then
  # Keep host-mounted project data writable for the local user's UID/GID.
  for directory in /app/data /app/data/outputs /app/data/work /app/cache /app/cache/huggingface; do
    chown "${APP_UID}:${APP_GID}" "$directory" 2>/dev/null || true
  done
  find /app/data -mindepth 1 -maxdepth 2 -type d -exec chown "${APP_UID}:${APP_GID}" {} + 2>/dev/null || true
  exec gosu "${APP_UID}:${APP_GID}" "$@"
fi

exec "$@"
