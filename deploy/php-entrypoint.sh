#!/bin/sh
set -eu

mkdir -p /data
chown root:www-data /data
chmod 2775 /data
if [ -f /data/pardubicko.db ]; then
  chown root:www-data /data/pardubicko.db /data/pardubicko.db-shm /data/pardubicko.db-wal 2>/dev/null || true
  chmod 0660 /data/pardubicko.db /data/pardubicko.db-shm /data/pardubicko.db-wal 2>/dev/null || true
fi

exec "$@"
