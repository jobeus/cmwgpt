# Configuration reference

This page documents the environment variables used by the current bot and log-viewer stack.

## Env file resolution

### Python bot

`src/config.py` loads environment variables from standard process environment and supports the current values exposed there.

### Log-viewer backend

`log-viewer/backend/src/env.ts` resolves env files in this order:

1. `CMWGPT_ENV_FILE` if set
2. `.env.production`
3. `.env`

The Docker dev stack instead passes values through `docker compose --env-file .env.development`.

## Required bot variables

- `DISCORD_BOT_TOKEN`
- `OPENROUTER_API_KEY`

## Downloader/API variables

- `RAPIDAPI_KEY`
  - required for Twitter/X context lookup
- `GROQ_API_KEY`
  - required for TikTok transcription
  - required for Facebook transcription
  - required for Twitter embedded-video transcription
- `TRANSCRIPT_PROXY`
  - optional proxy used by YouTube/article fetches and as a fallback for TikTok/Facebook downloads

## Database variables

- `DB_HOST`
- `DB_PORT`
- `DB_USER`
- `DB_PASSWORD`
- `DB_NAME`

These are used by both the Python logger and the log-viewer backend.

## Bot behavior variables

- `DEFAULT_MODEL`
  - fallback text model
  - default in code: `anthropic/claude-haiku-4.5`
- `DEFAULT_DRAW_MODEL`
  - default draw model key
- `DEFAULT_EDIT_MODEL`
  - default image edit model key
- `INCLUDE_USERNAMES`
  - whether Discord usernames are included in chat context
- `REPLY_TO_MENTIONS`
  - whether the bot answers mentions
- `INCLUDE_NUM_CHATLINES`
  - number of recent channel messages included in mention context
- `DISCORD_GUILD_ID`
  - guild scoping for slash command sync and some guild-specific features
- `DEATH_CHANNEL_ID`
  - where deathwatch notifications are posted
- `KEEP_UP_TO_DATE_WITH_GIT`
  - enables periodic git polling/restart behavior
- `QUIET_UPDATES`
  - suppresses restart/update announcements
- `MAX_CACHE_SIZE`
  - upper bound for persistent downloader caches

## Log-viewer backend variables

- `JWT_SECRET`
- `JWT_EXPIRES_IN`
- `LOG_VIEWER_ALLOWED_ORIGINS`
- `LOG_VIEWER_HOST`
- `LOG_VIEWER_PORT`
- `LOG_VIEWER_DEV_AUTH_ENABLED`
- `LOG_VIEWER_DEV_USERNAME`
- `LOG_VIEWER_DEV_PASSWORD`

Notes:

- In production, the backend expects `JWT_SECRET` and `LOG_VIEWER_ALLOWED_ORIGINS` to be set.
- In development, the backend can fall back to a dev JWT secret if needed.

## Log-viewer frontend variables

- `VITE_PORT`
- `VITE_PREVIEW_PORT`
- `VITE_API_URL`
  - defaults to `/api`
- `VITE_SOCKET_URL`
  - defaults to `/`
- `VITE_BACKEND_PROXY_TARGET`
  - Vite dev-server proxy target
- `VITE_DISCORD_GUILD_ID`

## Example layouts

### Minimal bot-only local setup

- Discord token
- OpenRouter key
- MariaDB credentials

Optional extras:

- Groq key for TikTok/Facebook/Twitter video transcription
- RapidAPI key for Twitter/X lookups

### Full Docker dev stack

Use `.env.development.example` as the starting point. It includes bot, MariaDB, backend, and frontend values that align with `docker-compose.yml`.

## Notes on legacy values

You may still see some older env names in sample files. This document only lists the variables that are part of the current runtime behavior and dev stack.
