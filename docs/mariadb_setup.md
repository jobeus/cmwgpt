# MariaDB setup

MariaDB is the shared storage layer for **request logs** and **pipeline logs**. If the database is wrong, the bot may still run, but your observability story gets much worse.

## What MariaDB is used for

| Consumer | What it uses the DB for |
| --- | --- |
| `src/db/logger.py` | Writing API request logs and pipeline-step logs |
| `src/db/connection.py` | Creating the async connection pool |
| `log-viewer/backend` | Reading log rows for the API and realtime stream |

## Core configuration variables

| Variable | Meaning |
| --- | --- |
| `DB_HOST` | MariaDB host |
| `DB_PORT` | MariaDB port |
| `DB_USER` | App user |
| `DB_PASSWORD` | App password |
| `DB_NAME` | Database containing the log schema |

## Two setup paths

| Path | Best for |
| --- | --- |
| Docker Compose | Fast local development |
| Existing/local MariaDB instance | Direct local hosting or external DB setups |

## Option 1: Docker Compose

This is the easiest path for local development.

```bash
cp .env.development.example .env.development
docker compose --env-file .env.development up --build db
```

### What Compose does for you

- starts MariaDB 11.7
- mounts `init_db.sql` into the init directory
- applies the schema automatically for a **fresh** database volume

### Important note

If you reuse an existing volume, MariaDB init scripts do not re-run automatically. In that case, treat schema changes explicitly.

## Option 2: Existing/local MariaDB instance

Create the database and user, then apply `init_db.sql`.

Example bootstrap:

```sql
CREATE DATABASE cmwgpt CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'cmwgpt_user'@'%' IDENTIFIED BY 'change-me-db';
GRANT ALL PRIVILEGES ON cmwgpt.* TO 'cmwgpt_user'@'%';
FLUSH PRIVILEGES;
```

Apply the schema with:

```bash
mariadb -u root -p cmwgpt < init_db.sql
```

## What the schema is responsible for

`init_db.sql` creates the tables required for:

- API request logging
- pipeline-step logging
- artifacts/replay metadata consumed by the log viewer

If the schema is missing or incompatible, failures often appear later and indirectly, for example when:

- a downloader or model request tries to log metadata
- a pipeline step tries to persist replay information
- the log-viewer backend queries recent logs and finds nothing usable

## Preflight checklist before starting the app

| Check | Why |
| --- | --- |
| Database is reachable at `DB_HOST:DB_PORT` | Basic connectivity |
| `DB_USER` can authenticate | Prevents write/read failures |
| `DB_NAME` exists | Avoids connection or query errors |
| `init_db.sql` has been applied | Required for logging and viewer functionality |

## Common local values

| Variable | Typical local value |
| --- | --- |
| `DB_HOST` | `127.0.0.1` |
| `DB_PORT` | `3306` |
| `DB_USER` | `cmwgpt_user` |
| `DB_NAME` | `cmwgpt` |

## Quick validation ideas

After setup, validate the DB path before blaming higher layers.

### Useful questions to answer

| Question | Why it helps |
| --- | --- |
| Can the app user connect? | Confirms credentials/grants |
| Does the schema exist? | Confirms init ran successfully |
| Do new requests create rows? | Confirms the write path works |
| Can the log-viewer backend read those rows? | Confirms observability end-to-end |

## Troubleshooting shortcuts

| Symptom | First thing to check |
| --- | --- |
| `Access denied` | `DB_USER`, `DB_PASSWORD`, grants |
| Connection refused/timeout | host, port, container/service health |
| Missing logs | schema applied and bot write path |
| Viewer empty but bot runs | backend DB access and schema presence |

## See also

- `docs/configuration.md`
- `docs/development.md`
- `docs/troubleshooting.md`
