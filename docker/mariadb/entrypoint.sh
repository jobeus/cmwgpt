#!/bin/sh
set -eu

export MARIADB_ROOT_PASSWORD="${MARIADB_ROOT_PASSWORD:-change-me-root}"
export MARIADB_DATABASE="${MARIADB_DATABASE:-${DB_NAME:-cmwgpt}}"
export MARIADB_USER="${MARIADB_USER:-${DB_USER:-cmwgpt_user}}"
export MARIADB_PASSWORD="${MARIADB_PASSWORD:-${DB_PASSWORD:-change-me-db}}"

exec docker-entrypoint.sh mariadbd

