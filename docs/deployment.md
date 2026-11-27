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
| Python 3.14 | Runtime used on the server, by CI and in the Docker image |
| Reachable Redis | Per-user memory (`/memory`, nightly job) depends on it; the bot still replies without it (see `docs/redis_setup.md`) |
| `DISCORD_BOT_TOKEN` | Discord authentication |
| `ANTHROPIC_API_KEY` | Text completions (the default model) and the memory job |
| `GEMINI_API_KEY` | `hydrogen`/`argon` models and image commands |
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

## How it is run for real (grizzly)

deployd, the server's deploy agent, builds each release and runs the bot from it inside tmux
under systemd (`tmux-app@cmwgpt`): attach with `tmux -L cmwgpt a` as jobe, logs in
`journalctl -t cmwgpt -f`. systemd starts it on boot and restarts it if it exits. Everything
is under `/srv/apps/cmwgpt`: `current` is the live release, `shared/` holds `.env` and the
files the bot keeps. The whole flow is in `docs/auto-update.md`.

`start.sh` loads `.env` and runs `main.py` from `.venv/`; `scripts/release-build.sh` builds
that `.venv/`. `make run` does both.

Anywhere else, `/restart` ends with the bot exiting with code `42`: run it under something
that starts it again (systemd, a loop) if you want restarts to come back by themselves.

## Full Docker stack

`docker-compose.yml` defines the local full-stack environment (the bot image is `python:3.14-slim`):

| Service | Purpose |
| --- | --- |
| `redis` | Redis 8 |
| `bot` | Python Discord bot |

Bring it up with:

```bash
docker compose --env-file .env.development up --build
```

### Ports

The bot listens on no port.

## Production-minded checklist

Even if you are not doing “real production,” these are the minimum responsible checks.

| Area | What to verify |
| --- | --- |
| Secrets | Real API keys |
| Redis | Listening on localhost only, `maxmemory-policy volatile-lru` so memories are never evicted |
| Restart behavior | Something restarts the bot when it exits (on grizzly: systemd, driven by deployd) |

## Updates

Deploys, `/restart` and package updates all end in a new release built by deployd; see
`docs/auto-update.md`.

## Recommended mental model

- Use the included Docker stack as a **development baseline**.
- Use a supervised Python process plus Redis for a **simple hosted runtime**.
- Treat auto-update as an **operational choice**, not a default convenience.

## See also

- `docs/development.md`
- `docs/configuration.md`
- `docs/redis_setup.md`
- `docs/auto-update.md`
