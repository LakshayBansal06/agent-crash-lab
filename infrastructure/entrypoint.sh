#!/bin/sh
# Hosts like Render mount the persistent disk owned by root. Fix ownership once, then run as the
# unprivileged app user. When the container already runs as that user (docker-compose), do nothing.
set -e
if [ "$(id -u)" = "0" ]; then
  mkdir -p /data
  chown -R crashlab:crashlab /data
  exec setpriv --reuid=crashlab --regid=crashlab --init-groups "$@"
fi
exec "$@"
