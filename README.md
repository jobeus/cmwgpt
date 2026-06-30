# CMWGPT

Mention-first Discord bot with URL-aware prompt enrichment, image generation/editing, and a MariaDB-backed log viewer.

## Start here

| If you want to... | Read this |
| --- | --- |
| Understand the whole system quickly | `docs/architecture.md` |
| See the current command surface | `docs/commands.md` |
| Configure env vars correctly | `docs/configuration.md` |
| Run the project locally or in Docker | `docs/development.md` |
| Understand the docs set at a glance | `docs/README.md` |

## What lives in this repo

| Area | Purpose |
| --- | --- |
| `src/` | Python Discord bot, services, startup wiring, downloader utilities, DB logging |
| `tests/` | Python unit/integration-style coverage for the bot and utilities |
| `log-viewer/backend/` | Node/TypeScript API + Socket.IO service for reading logs |
| `log-viewer/frontend/` | React/Vite UI for browsing API and pipeline logs |
| `docs/` | Architecture, commands, configuration, development, deployment, and troubleshooting docs |

## What the bot actually does

| Capability | Current behavior |
| --- | --- |
| Chat | Users mention the bot in Discord; there is no current `/chat` command |
| Context building | Recent channel history, reply context, embeds, and attachments are folded into the prompt |
| URL enrichment | The bot can inject transcripts/context from YouTube, TikTok, Twitter/X, Facebook, and generic articles |
| Images | Slash commands support image generation and image editing through `RunpodService` |
| Logging | API requests and pipeline steps are written to MariaDB |
| Observability | The log viewer backend/frontend read and stream those logs |

## Choose a workflow

### 1. Local bot development

Use this if you mainly want to work on the Python bot.

```bash
cp env.example .env
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -r test_requirements.txt
.venv/bin/python main.py
```

### 2. Full-stack development with Docker

Use this if you want bot + MariaDB + log viewer together.

```bash
cp .env.development.example .env.development
docker compose --env-file .env.development up --build
```

Default dev ports:

| Service | Port |
| --- | --- |
| MariaDB | `3306` |
| Log viewer backend | `3001` |
| Log viewer frontend | `5173` |

## Current user-facing commands

| Group | Commands |
| --- | --- |
| General | `/help`, `/model`, `/restart`, `/wikicount` |
| Prompting | `/systemprompt set`, `/systemprompt view`, `/systemprompt reset` |
| Images | `/draw`, `/drawmodel`, `/edit`, `/editmodel` |

| Deathwatch | `/death set`, `/death view`, `/death reset` |

Chat itself remains mention-based.

## Environment essentials

### Bot essentials

| Variable | Required | Why |
| --- | --- | --- |
| `DISCORD_BOT_TOKEN` | Yes | Connects the bot to Discord |
| `OPENROUTER_API_KEY` | Yes | Powers text-model completions via OpenRouter |
| `GEMINI_API_KEY` | If using `google`/`google-high`/`hybrid` models | Powers native Gemini completions |
| `RUNPOD_IO_API_KEY` | If using image commands | Powers draw/edit requests |

### Downloader/provider essentials

| Variable | Required when | Why |
| --- | --- | --- |
| `RAPIDAPI_KEY` | Using Twitter/X enrichment | Fetches tweet/context data |
| `GROQ_API_KEY` | Using TikTok/Facebook/Twitter-video transcription | Runs audio transcription |
| `TRANSCRIPT_PROXY` | Optional | Helps when direct transcript/media fetches fail |

### Database essentials

| Variable | Default | Why |
| --- | --- | --- |
| `DB_HOST` | `127.0.0.1` | MariaDB host |
| `DB_PORT` | `3306` | MariaDB port |
| `DB_USER` | `cmwgpt_user` | MariaDB user |
| `DB_PASSWORD` | empty | MariaDB password |
| `DB_NAME` | `cmwgpt` | Database name |

For the full reference, see `docs/configuration.md`.

## Architecture in one paragraph

`main.py` boots `src/startup.py`, which wires services like `StateService`, `QueueService`, `OpenAIService`, `GeminiService`, `RunpodService`, `DeathService`, and `MentionHandler` into `DiscordBotClient`. Mention replies are assembled by `src/bot/handlers/mention.py` (context built via `src/utils/chat_context.py`), enriched through `src/utils/downloader_utils.py`, then routed by `src/services/completion_dispatch.py` to OpenRouter, native Gemini, or the two-phase `hybrid` pipeline for text output, and logged into MariaDB. The log-viewer backend and frontend then query and stream those logs for inspection.

For diagrams and a deeper walkthrough, see `docs/architecture.md`.

## Testing

| What you want | Command |
| --- | --- |
| Default Python suite | `make test` |
| Verbose Python suite | `make test-verbose` |
| Coverage run | `make test-coverage` |
| Direct pytest | `.venv/bin/python -m pytest` |
| Frontend tests | `cd log-viewer/frontend && npm run test` |

More detail: `tests/README.md` and `log-viewer/frontend/README.md`.

## Important operational notes

- Prefer `make run-direct` for local bot development.
- `make run` uses `start.sh`, and the current wrapper assumes a `venv/` path while the Makefile creates `.venv/` by default.
- `/restart` triggers the restart handler, which saves state, performs a `git pull`, and exits with code `42`.

## Documentation map

The docs are meant to be read as a small system, not as isolated files:

- `docs/README.md` - docs index
- `docs/architecture.md` - diagrams + component map + request flow
- `docs/commands.md` - current command surface
- `docs/configuration.md` - env vars and defaults
- `docs/development.md` - local and Docker workflows
- `docs/deployment.md` - runtime/deployment notes
- `docs/mariadb_setup.md` - schema and DB setup
- `docs/troubleshooting.md` - common failure modes
