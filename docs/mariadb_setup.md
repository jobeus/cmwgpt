# MariaDB setup

> **Memory now lives in Redis** (see `redis_setup.md`). On its first start with Redis the bot copies the `user_memory` profiles and job state below into Redis once; after that MariaDB isn't used and can be shut down. The rest of this page is kept for that import.

MariaDB stores **per-user memory**: the short profiles the bot learns about each person, per server, and adds to mentions. It is not used for anything else. If the database is wrong, the bot still replies, but `/memory`, `/fillmemory` and the nightly memory job will not work.

## What MariaDB is used for

| Consumer | What it uses the DB for |
| --- | --- |
| `src/db/connection.py` | Creating the async connection pool |
| `src/db/memory_store.py` | Reading and writing the memory tables |
| `src/services/memory_service.py` | Nightly job, `/memory` edits, and the notes added to mentions |

## Core configuration variables

| Variable | Meaning |
| --- | --- |
| `DB_HOST` | MariaDB host |
| `DB_PORT` | MariaDB port |
| `DB_USER` | App user |
| `DB_PASSWORD` | App password |
| `DB_NAME` | Database containing the memory tables |

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

`init_db.sql` creates the per-user memory tables:

| Table | Holds |
| --- | --- |
| `user_memory` | One row per user per server: the profile |
| `user_memory_history` | A row for every change, so earlier versions can be restored |
| `user_memory_job_state` | How far the nightly job has processed each server |

Every statement in `init_db.sql` is `IF [NOT] EXISTS`, so re-applying it to an existing
database adds new tables and drops retired columns without touching anything else. With the docker-compose stack:

```bash
docker compose exec -T db sh -c 'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD"' < init_db.sql
```

If the schema is missing or incompatible, failures often appear later and indirectly, for example when:

- `/memory` commands reply with an error
- the nightly job logs a failure and profiles never change
- mentions quietly go out without any user notes (reply-time lookups never raise)

### Upgrading an older install

Older versions also created an `api_request_logs` table for request logging. Nothing uses it any more, so you can remove it:

```sql
DROP TABLE IF EXISTS api_request_logs;
```

## Preflight checklist before starting the app

| Check | Why |
| --- | --- |
| Database is reachable at `DB_HOST:DB_PORT` | Basic connectivity |
| `DB_USER` can authenticate | Prevents write/read failures |
| `DB_NAME` exists | Avoids connection or query errors |
| `init_db.sql` has been applied | Required for the memory features |

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
| Does `/memory correct` create a `user_memory` row? | Confirms the write path works |
| Does `/memory show` return it? | Confirms the read path end-to-end |

## Troubleshooting shortcuts

| Symptom | First thing to check |
| --- | --- |
| `Access denied` | `DB_USER`, `DB_PASSWORD`, grants |
| Connection refused/timeout | host, port, container/service health |
| `/memory` fails but bot runs | schema applied and `DB_*` values |
| Profiles never update | `MEMORY_MODEL` has an API key, and `user_memory_job_state.last_status` |

## See also

- `docs/configuration.md`
- `docs/development.md`
- `docs/troubleshooting.md`
