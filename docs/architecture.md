# Architecture

## High-level flow

The bot is a mention-first Discord application with a separate MariaDB-backed logging stack.

1. `main.py` starts the process.
2. `src/startup.py` loads config, creates shared services, and constructs the Discord client.
3. `src/bot/client.py` registers slash commands and message handlers.
4. `src/bot/handlers/mention.py` assembles conversation context for mentions.
5. `src/utils/downloader_utils.py` enriches prompts with transcripts/article text for supported URLs.
6. `src/services/openai_service.py` sends text requests to OpenRouter.
7. `src/services/runpod_service.py` handles image generation/editing.
8. `src/db/logger.py` writes API and pipeline events to MariaDB for the log viewer.

## Main Python entry points

- `main.py` - async startup/shutdown wrapper
- `src/startup.py` - service wiring and client construction
- `src/config.py` - environment loading and config defaults

## Discord layer

### Client

`src/bot/client.py` is responsible for:

- connecting to Discord
- registering slash commands
- routing messages to the mention pipeline
- starting auxiliary services on ready
- coordinating graceful shutdown and restart handling

### Command modules

- `src/bot/commands/system.py`
- `src/bot/commands/image.py`
- `src/bot/commands/interject.py`
- `src/bot/commands/death.py`

The current bot does not expose a `/chat` command. User chat requests happen by mentioning the bot.

### Mention handling

`src/bot/handlers/mention.py` is the central request pipeline for conversational replies. It:

- detects and queues mention work
- optionally includes usernames and reply-chain context
- pulls recent channel history according to `INCLUDE_NUM_CHATLINES`
- injects downloader output for supported URLs
- forwards the assembled prompt to `OpenAIService`
- persists response continuity through `StateService`

## Services

### `StateService`

`src/services/state_service.py` stores per-channel state, including:

- selected text model
- selected draw/edit models
- custom system prompt
- response continuity IDs for the mention flow
- persisted settings for interject/death services

### `QueueService`

`src/services/queue_service.py` serializes command and mention work and enforces a bounded queue.

### `OpenAIService`

`src/services/openai_service.py` currently handles text responses through OpenRouter chat completions.

- default fallback model: `anthropic/claude-haiku-4.5`
- optional web-search tool support for select Gemini models

### `RunpodService`

`src/services/runpod_service.py` handles image work.

Supported draw/edit model keys are defined in code and exposed through slash-command choices.

### `PasteService`

`src/services/paste_service.py` uploads oversized content and can inject resulting paste text into the article cache.

### `MessageService`

`src/services/message_service.py` centralizes Discord response/edit/send helpers.

### `AutoUpdateService` and `RestartHandler`

- `src/services/auto_update_service.py` polls git for updates when enabled
- `src/services/restart_handler.py` saves state, performs `git pull`, and exits with restart code `42`

### `AnnouncementService`

`src/services/announcement_service.py` restores pending restart/update announcements on startup.

### `InterjectService`

`src/services/interject_service.py` can occasionally inject bot messages into active channels based on configurable probability and cooldown settings.

### `DeathService`

`src/services/death_service.py` periodically checks the configured guild's members against the Wikipedia death feed/pageview thresholds and can post notifications to `DEATH_CHANNEL_ID`.

## Downloader and enrichment pipeline

`src/utils/downloader_utils.py` discovers supported URLs in a message and aggregates any successful fetches into a text block that gets prepended to the model prompt.

Current supported enrichments:

- YouTube transcripts via `src/utils/youtube_utils.py`
- TikTok transcripts via `src/utils/tiktok_utils.py`
- Twitter/X context via `src/utils/twitter_utils.py`
- Facebook video transcripts via `src/utils/facebook_utils.py`
- Generic article extraction via `src/utils/url_utils.py`

Most downloader helpers use `PersistentCache` from `src/utils/cache_utils.py` so successful fetches survive process restarts.

## Database and observability

### MariaDB connection

- `src/db/connection.py` builds the async MariaDB pool
- `init_db.sql` creates the required tables

### Logging model

`src/db/logger.py` writes two main kinds of records:

- API request logs
- pipeline step logs, including replay snippets and artifacts

This logging is what powers the log viewer.

## Log viewer

### Backend

`log-viewer/backend` is the Node/TypeScript service that:

- authenticates viewers
- queries MariaDB log rows
- serves media through proxy routes
- streams updates over Socket.IO

### Frontend

`log-viewer/frontend` is the React/Vite UI that:

- provides login
- shows recent logs
- opens per-log detail views
- renders conversation/pipeline panels and replay helpers

## Runtime layouts

### Local bot-only development

- Python process started directly from `main.py`
- external MariaDB instance or local DB

### Docker development stack

`docker-compose.yml` runs:

- `db`
- `bot`
- `backend`
- `frontend`

This is the simplest way to run the whole observability stack together.
