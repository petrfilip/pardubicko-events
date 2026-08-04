#!/bin/sh
set -eu

mkdir -p /data /snapshots /backups
chown root:www-data /data /snapshots /backups
chmod 2775 /data /snapshots /backups
umask 0002

set +e
"$@"
status=$?
set -e
find /data -maxdepth 1 -type f -name 'pardubicko.db*' -exec chgrp www-data {} \; -exec chmod 0660 {} \; 2>/dev/null || true
exit "$status"
