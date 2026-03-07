# CMWGPT

Discord bot for mention-driven chat, image generation/editing, automatic URL enrichment, and MariaDB-backed request logging.

## What it does

- Responds when the bot is mentioned in Discord.
- Augments prompts with content pulled from supported URLs:
  - YouTube transcripts
  - TikTok audio transcripts
  - Twitter/X post context, plus embedded video transcription when available
  - Facebook video transcripts
  - Generic article extraction
- Supports slash commands for model selection, system prompts, image generation/editing, interjections, deathwatch posts, and restart.
- Logs API and pipeline activity to MariaDB for the log viewer.

## Architecture at a glance

- `main.py` starts the app and delegates wiring to `src/startup.py`.
- `src/bot/client.py` owns the Discord client, slash-command registration, and startup/shutdown lifecycle.
- `src/bot/handlers/mention.py` builds mention context, injects downloader output, and calls the model.
- `src/services/openai_service.py` talks to OpenRouter for text responses.
- `src/services/runpod_service.py` handles image generation and image editing models.
- `src/db/logger.py` and `src/db/connection.py` persist request and pipeline logs to MariaDB.
- `log-viewer/backend` and `log-viewer/frontend` provide the web UI for those logs.

See also:

- `docs/architecture.md`
- `docs/commands.md`
- `docs/configuration.md`
- `docs/deployment.md`
- `docs/development.md`
- `docs/mariadb_setup.md`

## Quick start

### Local Python bot

1. Create an env file from `env.example`.
2. Create a virtualenv and install dependencies.
3. Start MariaDB and initialize `init_db.sql`.
4. Run the bot.

Suggested commands:

```bash
cp env.example .env
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -r test_requirements.txt
.venv/bin/python main.py
```

`make venv install install-test` also works for setup. For local runs, prefer `make run-direct` over `make run`; the auto-restart wrapper in `start.sh` assumes a `venv/` path, while the Makefile creates `.venv/` by default.

### Docker development stack

The repo includes a compose stack for:

- `db` (MariaDB)
- `bot` (Python bot)
- `backend` (log-viewer API/socket server)
- `frontend` (Vite React UI)

Suggested flow:

```bash
cp .env.development.example .env.development
docker compose --env-file .env.development up --build
```

Default dev ports:

- MariaDB: `3306`
- Log viewer backend: `3001`
- Log viewer frontend: `5173`

## Core environment variables

Required for the bot:

- `DISCORD_BOT_TOKEN`
- `OPENROUTER_API_KEY`

Required for specific downloader features:

- `GROQ_API_KEY` for TikTok/Facebook audio transcription and Twitter video transcription
- `RAPIDAPI_KEY` for Twitter/X context fetches

Required for database-backed logging:

- `DB_HOST`
- `DB_PORT`
- `DB_USER`
- `DB_PASSWORD`
- `DB_NAME`

Important optional values:

- `DEFAULT_MODEL`
- `DEFAULT_DRAW_MODEL`
- `DEFAULT_EDIT_MODEL`
- `REPLY_TO_MENTIONS`
- `INCLUDE_USERNAMES`
- `INCLUDE_NUM_CHATLINES`
- `TRANSCRIPT_PROXY`
- `KEEP_UP_TO_DATE_WITH_GIT`
- `QUIET_UPDATES`
- `DISCORD_GUILD_ID`
- `DEATH_CHANNEL_ID`
- `MAX_CACHE_SIZE`

See `docs/configuration.md` for the full grouped reference, including log-viewer variables.

## Current slash commands

- `/help`
- `/model`
- `/systemprompt set|view|reset`
- `/draw`
- `/drawmodel`
- `/edit`
- `/editmodel`
- `/interject set|view|reset|count`
- `/death set|view|reset`
- `/restart`

The main chat path is still mention-based: mention the bot in a message rather than using a `/chat` command.

## Tests

Run the Python test suite with one of:

```bash
make test
make test-verbose
.venv/bin/python -m pytest
```

More detail: `tests/README.md`

## Log viewer

- Backend: `log-viewer/backend`
- Frontend: `log-viewer/frontend`

The frontend talks to the backend over HTTP and Socket.IO and expects the MariaDB log tables created by `init_db.sql`.

## Status of the docs

This README and the files under `docs/`, `tests/README.md`, and `log-viewer/frontend/README.md` are intended to match the current codebase layout and behavior.
