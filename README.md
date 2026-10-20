# CMWGPT

Mention-first Discord bot with URL-aware prompt enrichment, image generation/editing, and Redis-backed per-user memory.

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
| `src/` | Python Discord bot, services, startup wiring, downloader utilities, memory storage |
| `tests/` | Python unit/integration-style coverage for the bot and utilities |
| `docs/` | Architecture, commands, configuration, development, deployment, and troubleshooting docs |

## What the bot actually does

| Capability | Current behavior |
| --- | --- |
| Chat | Users mention the bot in Discord; there is no current `/chat` command |
| Context building | Recent channel history, reply context, embeds, and attachments are folded into the prompt |
| URL enrichment | The bot can inject transcripts/context from YouTube, TikTok, Twitter/X, Facebook, and generic articles |
| Images | Slash commands support image generation and image editing through `RunpodService` |
| Memory | A nightly job learns short per-user profiles (stored in Redis) and adds them to mentions; `/memory` shows and edits them |
| Observability | Python logging goes to stdout and `logs/`; there is no request log table or log viewer |

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

Use this if you want bot + Redis together.

```bash
cp .env.development.example .env.development
docker compose --env-file .env.development up --build
```

Default dev ports:

| Service | Port |
| --- | --- |
Redis isn't exposed on the host. The bot itself does not listen on any port.

## Current user-facing commands

| Group | Commands |
| --- | --- |
| General | `/help`, `/model`, `/restart`, `/wikicount` |
| Prompting | `/systemprompt set`, `/systemprompt view`, `/systemprompt reset` |
| Images | `/draw`, `/drawmodel`, `/edit`, `/editmodel` |
| Memory | `/memory show`, `/memory correct`, `/memory clear`, `/fillmemory` |
| Deathwatch | `/death set`, `/death view`, `/death reset` |

Chat itself remains mention-based.

## Environment essentials

### Bot essentials

| Variable | Required | Why |
| --- | --- | --- |
| `DISCORD_BOT_TOKEN` | Yes | Connects the bot to Discord |
| `OPENROUTER_API_KEY` | Yes | Powers text-model completions via OpenRouter |
| `GEMINI_API_KEY` | If using `google`/`google-high` models | Powers native Gemini completions |
| `RUNPOD_IO_API_KEY` | If using image commands | Powers draw/edit requests |

### Downloader/provider essentials

| Variable | Required when | Why |
| --- | --- | --- |
| `RAPIDAPI_KEY` | Using Twitter/X enrichment | Fetches tweet/context data |
| `GROQ_API_KEY` | Using TikTok/Facebook/Twitter-video transcription | Runs audio transcription |
| `TRANSCRIPT_PROXY` | Optional | Helps when direct transcript/media fetches fail |

### Storage

| Variable | Default | Why |
| --- | --- | --- |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis for per-user memory and content caches |

Redis holds per-user memory and the content caches; see `docs/redis_setup.md`.

For the full reference, see `docs/configuration.md`.

## Architecture in one paragraph

`main.py` boots `src/startup.py`, which wires services like `StateService`, `QueueService`, `OpenAIService`, `GeminiService`, `RunpodService`, `DeathService`, `MemoryService`, and `MentionHandler` into `DiscordBotClient`. Mention replies are assembled by `src/bot/handlers/mention.py` (context built via `src/utils/chat_context.py`), enriched through `src/utils/downloader_utils.py`, then routed by `src/services/completion_dispatch.py` to OpenRouter, native Gemini, or the Claude API for text output. Outbound URL fetches go through `src/utils/http_client.py`, which re-checks the SSRF guard on every redirect and caps body size and total time. Redis holds the per-user memory that `MemoryService` learns nightly and adds to mentions, plus the URL content caches.

For diagrams and a deeper walkthrough, see `docs/architecture.md`.

## Testing

| What you want | Command |
| --- | --- |
| Default Python suite | `make test` |
| Verbose Python suite | `make test-verbose` |
| Coverage run | `make test-coverage` |
| Direct pytest | `.venv/bin/python -m pytest` |

More detail: `tests/README.md`.

## Important operational notes

- Prefer `make run-direct` for local bot development.
- `make run` uses `start.sh`, which uses `.venv/` (or a legacy `venv/`) and reruns the bot whenever it exits with code `42`. On a server, run `start.sh` under systemd (`deploy/cmwgpt.service`) so crashes and reboots are covered too.
- Pushes to `main` that pass CI notify the bot through a signed webhook, and it pulls and restarts (`docs/auto-update.md`).
- `/restart` triggers the restart handler, which saves state, performs a `git pull`, runs the normal shutdown sequence (stop jobs, drain the queue, close clients, close Redis), and exits with code `42` so `start.sh` or your process manager starts it again.

## Documentation map

The docs are meant to be read as a small system, not as isolated files:

- `docs/README.md` - docs index
- `docs/architecture.md` - diagrams + component map + request flow
- `docs/commands.md` - current command surface
- `docs/configuration.md` - env vars and defaults
- `docs/development.md` - local and Docker workflows
- `docs/deployment.md` - runtime/deployment notes
- `docs/redis_setup.md` - Redis setup for memory and caches
- `docs/troubleshooting.md` - common failure modes
