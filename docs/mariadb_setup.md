# MariaDB setup

This project logs API requests and pipeline steps to MariaDB. The schema is defined in `init_db.sql`.

## What uses MariaDB

- the Python bot logging layer in `src/db/logger.py`
- the Python connection pool in `src/db/connection.py`
- the log-viewer backend in `log-viewer/backend`

## Required variables

- `DB_HOST`
- `DB_PORT`
- `DB_USER`
- `DB_PASSWORD`
- `DB_NAME`

## Option 1: Docker compose

The easiest development setup is the repository compose stack.

```bash
cp .env.development.example .env.development
docker compose --env-file .env.development up --build db
```

Compose mounts `init_db.sql` into MariaDB's init directory, so a fresh volume gets the schema automatically.

Default dev image/version in the compose file:

- `mariadb:11.7`

## Option 2: Existing/local MariaDB instance

Create the database and user, then apply `init_db.sql`.

Example:

```sql
CREATE DATABASE cmwgpt CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'cmwgpt_user'@'%' IDENTIFIED BY 'change-me-db';
GRANT ALL PRIVILEGES ON cmwgpt.* TO 'cmwgpt_user'@'%';
FLUSH PRIVILEGES;
```

Then run:

```bash
mariadb -u root -p cmwgpt < init_db.sql
```

## Current schema responsibilities

`init_db.sql` creates the tables required for request logging and pipeline tracing. Those rows are later consumed by the log viewer.

If the schema is missing or incompatible, you will typically see failures when:

- downloader or model calls attempt to log API requests
- pipeline steps try to persist artifacts/replay metadata
- the log-viewer backend queries recent logs

## Connectivity checklist

Before starting the bot, verify:

1. the database is reachable at `DB_HOST:DB_PORT`
2. the configured user can connect
3. the configured database exists
4. `init_db.sql` has been applied

## Common local values

For local development without Docker:

- `DB_HOST=127.0.0.1`
- `DB_PORT=3306`
- `DB_USER=cmwgpt_user`
- `DB_NAME=cmwgpt`

## Troubleshooting

- `Access denied`: re-check `DB_USER`/`DB_PASSWORD` and grants
- connection timeout/refused: verify host, port, and container/service health
- missing log rows: confirm the schema exists and that the bot can write to it

See also `docs/troubleshooting.md`.
