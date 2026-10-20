# Deployment and runtime guide

This page is about **how to run the system responsibly**, not just how to start a process.

## Choose a deployment shape

| Shape | Good for | What you run |
| --- | --- | --- |
| Bot-only runtime | Hosting the Python bot, with a local Redis for per-user memory and caches | `python main.py` under a supervisor |
| Full Docker stack | Development and local end-to-end testing | `docker compose --env-file .env.development up --build` |

## Important expectation-setting

The repository ships a **good development stack**, not a hardened production platform.

What it does provide well:

- a runnable Python bot
- Redis-backed per-user memory
- a compose setup that brings the bot and Redis up together

What it does **not** currently try to be:

- a Kubernetes deployment
- a full production IaC package
- a security-hardened, production-ready compose bundle

## Bot-only deployment checklist

### Prerequisites

| Requirement | Why it matters |
| --- | --- |
| Python 3.13 | Runtime used by CI and the Docker image |
| Reachable Redis | Per-user memory (`/memory`, nightly job) depends on it; the bot still replies without it (see `docs/redis_setup.md`) |
| `DISCORD_BOT_TOKEN` | Discord authentication |
| `OPENROUTER_API_KEY` | Text completions |
| `RUNPOD_IO_API_KEY` | Needed if image commands are used |
| Optional `RAPIDAPI_KEY` / `GROQ_API_KEY` | Needed for some URL-enrichment providers |

### Core rollout steps

1. Create and populate the env file.
2. Install Python dependencies.
3. Install and configure Redis (`docs/redis_setup.md`).
4. Start the bot under a real process supervisor.

Typical install/run shape:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

## Why a process supervisor is not optional

The restart flow saves state, runs `git pull`, then runs the normal shutdown sequence (stop background jobs, drain the queue for up to 90 seconds, close clients, close Redis) and intentionally exits with code `42`.

That means the bot expects an outer process manager to bring it back up after:

- a manual `/restart`
- an auto-update restart

Recommended supervisors include:

- `systemd`
- `supervisord`
- `pm2`
- any equivalent service manager that reliably restarts on exit

`start.sh` (used by `make run`) is a minimal wrapper: it reruns `main.py` after exit `42`, reinstalling requirements first. It does not survive a crash, a reboot, or closing the terminal it runs in, so on a host run it under systemd with `deploy/cmwgpt.service` (instructions are in the file's header). systemd restarts `start.sh` on anything else and starts it on boot; logs go to `journalctl -u cmwgpt -f`.

### Supervisor behavior you want

| Behavior | Why |
| --- | --- |
| Restart on non-zero exit | Handles intentional restart exit `42` |
| Start on boot | Keeps the bot available after host restarts |
| Persistent logs | Makes startup/restart issues much easier to debug |
| Controlled working directory | Important if git-based restart/update is enabled |

## Full Docker stack

`docker-compose.yml` defines the local full-stack environment (the bot image is `python:3.13-slim`):

| Service | Purpose |
| --- | --- |
| `redis` | Redis 7.4 |
| `bot` | Python Discord bot |

Bring it up with:

```bash
docker compose --env-file .env.development up --build
```

### Default dev ports

| Port | Service |
| --- | --- |
The bot only listens on a port when `DEPLOY_WEBHOOK_SECRET` is set (the deploy webhook, `DEPLOY_WEBHOOK_PORT`, default `8787`).

## Production-minded checklist

Even if you are not doing “real production,” these are the minimum responsible checks.

| Area | What to verify |
| --- | --- |
| Secrets | Real API keys |
| Redis | Listening on localhost only, `maxmemory-policy volatile-lru` so memories are never evicted |
| Restart behavior | Supervisor restarts the bot after exit `42` |
| Git-based updates | Only set `DEPLOY_WEBHOOK_SECRET` where `git pull` is safe and expected |

## Auto-update and git-based restart considerations

If you enable the deploy webhook (`DEPLOY_WEBHOOK_SECRET`), the deployment must satisfy all of these:

| Requirement | Why |
| --- | --- |
| Valid git checkout | Restart flow performs `git pull` |
| Correct remote/auth setup | Update path must be able to fetch/pull |
| Writable working tree | Pulls will fail in read-only or locked environments |
| Process supervisor | Bot exits after restart preparation |

If those assumptions are not true, leave `DEPLOY_WEBHOOK_SECRET` empty.

## Recommended mental model

- Use the included Docker stack as a **development baseline**.
- Use a supervised Python process plus Redis for a **simple hosted runtime**.
- Treat auto-update as an **operational choice**, not a default convenience.

## See also

- `docs/development.md`
- `docs/configuration.md`
- `docs/redis_setup.md`
- `docs/auto-update.md`
