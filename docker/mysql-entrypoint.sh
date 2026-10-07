#!/bin/bash
set -eu

# The MySQL image creates root itself and rejects MYSQL_USER=root.
# A local .env that signs in as root can keep MYSQL_PASSWORD only.
if [ "${MYSQL_USER:-}" = "root" ]; then
  if [ -z "${MYSQL_ROOT_PASSWORD:-}" ]; then
    export MYSQL_ROOT_PASSWORD="${MYSQL_PASSWORD:-}"
  fi
  unset MYSQL_USER
  unset MYSQL_PASSWORD
fi

if [ -z "${MYSQL_ROOT_PASSWORD:-}" ]; then
  echo "Set MYSQL_PASSWORD in .env. When MYSQL_USER is not root, also set MYSQL_ROOT_PASSWORD." >&2
  exit 1
fi

exec /usr/local/bin/docker-entrypoint.sh "$@"
