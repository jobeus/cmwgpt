# Deployment and runtime guide

This page is about **how to run the system responsibly**, not just how to start a process.

## Choose a deployment shape

| Shape | Good for | What you run |
| --- | --- | --- |
| Bot-only runtime | Hosting the Python bot with an existing/local MariaDB instance | `python main.py` under a supervisor |
| Full Docker stack | Development and local end-to-end testing | `docker compose --env-file .env.development up --build` |

## Important expectation-setting

The repository ships a **good development stack**, not a hardened production platform.

What it does provide well:

- a runnable Python bot
- MariaDB-backed request/pipeline logging
- a separate backend/frontend log viewer
- a compose setup that brings the whole stack up together

What it does **not** currently try to be:

- a Kubernetes deployment
- a full production IaC package
- a migration-managed database platform
- a security-hardened, production-ready compose bundle

## Bot-only deployment checklist

### Prerequisites

| Requirement | Why it matters |
| --- | --- |
| Python 3.11+ | Required runtime for the bot |
| Reachable MariaDB instance | Logging and viewer data depend on it |
| `DISCORD_BOT_TOKEN` | Discord authentication |
| `OPENROUTER_API_KEY` | Text completions |
| `RUNPOD_IO_API_KEY` | Needed if image commands are used |
| Optional `RAPIDAPI_KEY` / `GROQ_API_KEY` | Needed for some URL-enrichment providers |

### Core rollout steps

1. Create and populate the env file.
2. Install Python dependencies.
3. Apply `init_db.sql` to the target database.
4. Start the bot under a real process supervisor.

Typical install/run shape:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

## Why a process supervisor is not optional

The restart flow intentionally exits with code `42`.

That means the bot expects an outer process manager to bring it back up after:

- a manual `/restart`
- an auto-update restart

Recommended supervisors include:

- `systemd`
- `supervisord`
- `pm2`
- any equivalent service manager that reliably restarts on exit

### Supervisor behavior you want

| Behavior | Why |
| --- | --- |
| Restart on non-zero exit | Handles intentional restart exit `42` |
| Start on boot | Keeps the bot available after host restarts |
| Persistent logs | Makes startup/restart issues much easier to debug |
| Controlled working directory | Important if git-based restart/update is enabled |

## Full Docker stack

`docker-compose.yml` defines the local full-stack environment:

| Service | Purpose |
| --- | --- |
| `db` | MariaDB 11.7 |
| `bot` | Python Discord bot |
| `backend` | Log-viewer API + Socket.IO service |
| `frontend` | Vite/React UI |

Bring it up with:

```bash
docker compose --env-file .env.development up --build
```

### Default dev ports

| Port | Service |
| --- | --- |
| `3306` | MariaDB |
| `3001` | Log-viewer backend |
| `5173` | Log-viewer frontend |

### Runtime notes for the backend/frontend side

- In Docker, the backend container maps `PORT` from `LOG_VIEWER_PORT`.
- The backend host is typically `0.0.0.0` in Docker and `127.0.0.1` by default outside it.
- The frontend dev server proxies `/api` and `/socket.io` to the backend target.

## Production-minded checklist

Even if you are not doing “real production,” these are the minimum responsible checks.

| Area | What to verify |
| --- | --- |
| Secrets | Strong `JWT_SECRET`, real DB credentials, real API keys |
| Origins | `LOG_VIEWER_ALLOWED_ORIGINS` set correctly |
| Database | `init_db.sql` applied before services expect logs |
| Restart behavior | Supervisor restarts the bot after exit `42` |
| Git-based updates | Only enable `KEEP_UP_TO_DATE_WITH_GIT` where `git pull` is safe and expected |
| Auth mode | Do not rely on dev auth settings in production |

## Auto-update and git-based restart considerations

If you enable `KEEP_UP_TO_DATE_WITH_GIT`, the deployment must satisfy all of these:

| Requirement | Why |
| --- | --- |
| Valid git checkout | Restart flow performs `git pull` |
| Correct remote/auth setup | Update path must be able to fetch/pull |
| Writable working tree | Pulls will fail in read-only or locked environments |
| Process supervisor | Bot exits after restart preparation |

If those assumptions are not true, keep `KEEP_UP_TO_DATE_WITH_GIT=false`.

## Recommended mental model

- Use the included Docker stack as a **development baseline**.
- Use a supervised Python process plus MariaDB for a **simple hosted runtime**.
- Treat auto-update as an **operational choice**, not a default convenience.

## See also

- `docs/development.md`
- `docs/configuration.md`
- `docs/mariadb_setup.md`
- `docs/auto-update.md`
