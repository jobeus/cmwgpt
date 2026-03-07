# Development guide

## Repository areas

- `src/` - Python bot code
- `tests/` - Python test suite
- `docs/` - project documentation
- `log-viewer/backend/` - Node/TypeScript backend for log inspection
- `log-viewer/frontend/` - React/Vite frontend for log inspection

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

## Running the bot

### Recommended local run

```bash
make run-direct
```

This executes `.venv/bin/python main.py` and avoids the legacy restart wrapper assumptions in `start.sh`.

### Auto-restart wrapper

```bash
make run
```

Use this only if your local environment matches the wrapper's expectations; the current script assumes a `venv/` path when reinstalling after restart.

## Running the full dev stack

For bot + database + log viewer together:

```bash
cp .env.development.example .env.development
docker compose --env-file .env.development up --build
```

## Python tests

### Common commands

```bash
make test
make test-verbose
make test-coverage
```

You can also run pytest directly:

```bash
.venv/bin/python -m pytest
```

### Focused tests

```bash
make test-specific TEST=config
```

That maps to `tests/run_tests.py` and loads `test_<name>.py`.

## Static checks

### Lint

```bash
make lint
```

### Type check

```bash
make typecheck
```

### Format

```bash
make format
```

### CI-style local pass

```bash
make ci-test
```

## Frontend/backend development

### Log-viewer backend

The backend lives in `log-viewer/backend` and is typically run through Docker in this repo.

### Log-viewer frontend

The frontend lives in `log-viewer/frontend` and uses:

- React
- Vite
- TypeScript
- Tailwind CSS
- Socket.IO client

See `log-viewer/frontend/README.md` for frontend-specific commands.

## Debugging tips

- Verify env vars first, especially API keys and DB credentials.
- If mention replies are missing, confirm `REPLY_TO_MENTIONS=true`.
- If slash commands look stale, check `DISCORD_GUILD_ID` and command sync behavior.
- If downloader enrichment is missing, check the source-specific keys: `RAPIDAPI_KEY`, `GROQ_API_KEY`, and `TRANSCRIPT_PROXY`.
