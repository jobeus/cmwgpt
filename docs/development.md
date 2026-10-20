# Development guide

This page is optimized for the three workflows people actually use in this repo:

1. **work on the Python bot locally**
2. **run the full stack with Docker**
3. **iterate on tests/checks quickly**

## Pick a workflow

| Workflow | Best when | Main command path |
| --- | --- | --- |
| Local bot-only | You are changing Python bot logic and do not need the Docker stack | `.venv` + `python main.py` |
| Full-stack Docker | You want bot + DB together | `docker compose --env-file .env.development up --build` |
| Focused test/debug loop | You are iterating on a single area | `make test-specific`, direct `pytest` |

## Local Python setup

### Create the environment

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -r test_requirements.txt
```

Equivalent Make targets:

```bash
make venv
make install
make install-test
```

## Running the bot locally

### Recommended

```bash
make run-direct
```

Why this is preferred:

- it runs `.venv/bin/python main.py` directly
- it avoids wrapper assumptions that can be surprising during development

### Less recommended: wrapper-based run

```bash
make run
```

This uses `start.sh`, which runs `main.py` from `.venv/` (or a legacy `venv/`) and, whenever the bot exits with code `42` (after `/restart` or an auto-update), reinstalls requirements and starts it again.

## Running the full stack with Docker

```bash
cp .env.development.example .env.development
docker compose --env-file .env.development up --build
```

This starts:

| Service | Purpose |
| --- | --- |
| `redis` | Redis for per-user memory and content caches |
| `bot` | Python Discord bot (`python:3.13-slim` image) |

CI runs only the Python job, on Python 3.13.

## Fast command cheat sheet

| Goal | Command |
| --- | --- |
| Run Python tests | `make test` |
| Run verbose Python tests | `make test-verbose` |
| Run coverage | `make test-coverage` |
| Run a specific test module | `make test-specific TEST=downloader_utils` |
| Run pytest directly | `.venv/bin/python -m pytest` |
| Lint Python | `make lint` |
| Type-check Python | `make typecheck` |
| Format Python | `make format` |
| CI-style local sweep | `make ci-test` |

## Targeted iteration loops

### Working on mention behavior or downloader logic

Use one of:

```bash
.venv/bin/python -m pytest tests/test_mention_handler.py
.venv/bin/python -m pytest tests/test_downloader_utils.py
```

### Working on a specific service

Examples:

```bash
.venv/bin/python -m pytest tests/test_state_service.py
.venv/bin/python -m pytest tests/test_memory_service.py
```

## Debugging heuristics that save time

| Symptom | Check first |
| --- | --- |
| Bot does not answer mentions | `REPLY_TO_MENTIONS`, Discord permissions, correct mention path |
| Commands look stale | `DISCORD_GUILD_ID`, command sync behavior, whether the bot restarted |
| URL enrichment missing | `RAPIDAPI_KEY`, `GROQ_API_KEY`, `TRANSCRIPT_PROXY` |
| `/memory` errors or no profiles in mentions | Redis connectivity (`redis-cli ping`) and `REDIS_URL` |
| Restart flow weirdness | whether you used `make run` vs `make run-direct` |

## Suggested file-entry points for contributors

| Goal | Open this first |
| --- | --- |
| Understand service wiring | `src/startup.py` |
| Understand runtime orchestration | `src/bot/client.py` |
| Understand mention requests | `src/bot/handlers/mention.py` |
| Understand downloader behavior | `src/utils/downloader_utils.py` |
| Understand per-user memory | `src/services/memory_service.py`, `src/db/memory_store.py` |
| Understand outbound fetches | `src/utils/http_client.py`, `src/utils/ssrf_guard.py` |
