# Deployment and runtime notes

## Supported runtime shapes

The repository currently supports two practical modes:

1. **Python bot only** with an external or local MariaDB instance
2. **Full Docker development stack** with bot, MariaDB, log-viewer backend, and log-viewer frontend

## Python bot deployment

### Requirements

- Python 3.11+
- MariaDB reachable from the bot
- valid Discord/OpenRouter credentials

### Basic steps

1. Create and populate an env file.
2. Install Python dependencies from `requirements.txt`.
3. Initialize the database with `init_db.sql`.
4. Run `python main.py` under a process manager.

Recommended process managers include `systemd`, `supervisord`, `pm2`, or any other service runner that will restart the process when it exits.

### Why a process manager matters

The restart path exits the process with code `42`. That is intentional and is how the bot signals a self-restart after saving state and pulling new code.

## Docker stack

`docker-compose.yml` defines:

- `db` - MariaDB 11.7
- `bot` - Python bot container
- `backend` - log-viewer backend
- `frontend` - Vite frontend

Bring it up with:

```bash
docker compose --env-file .env.development up --build
```

The compose file mounts the repo into containers for development-oriented workflows.

## Ports

Default exposed ports in the development stack:

- `3306` - MariaDB
- `3001` - log-viewer backend
- `5173` - log-viewer frontend

## Production considerations

### Secrets

- set strong `JWT_SECRET`
- set real database credentials
- do not rely on development auth settings in production

### CORS/origins

Set `LOG_VIEWER_ALLOWED_ORIGINS` for the deployed frontend host(s).

### Database initialization

Run `init_db.sql` before starting services that need request logging.

### Restarts and updates

If you enable `KEEP_UP_TO_DATE_WITH_GIT`, make sure the deployment environment:

- has a valid git checkout
- can perform `git pull`
- is supervised by a process manager that restarts the bot after exit

## What this repo does not currently provide

- Kubernetes manifests
- a hardened production compose profile
- automated migrations beyond the current SQL initialization file

Use the included compose stack as a development baseline, not as a drop-in production blueprint.
