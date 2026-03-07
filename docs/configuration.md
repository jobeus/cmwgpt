# Configuration reference

This page focuses on the env vars that matter in the **current** runtime: the Python bot, MariaDB logging, the log-viewer backend, and the React frontend.

## Which env file should you edit?

| Workflow | Recommended file |
| --- | --- |
| Local bot-only development | `.env` (usually copied from `env.example`) |
| Docker dev stack | `.env.development` (copied from `.env.development.example`) |
| Production log-viewer backend | `.env.production` or `CMWGPT_ENV_FILE` |

### Backend env-file resolution

The log-viewer backend resolves env files in this order:

1. `CMWGPT_ENV_FILE`
2. `.env.production`
3. `.env`

## Bot/runtime variables

These are loaded by `src/config.py`.

| Variable | Default | Required | Notes |
| --- | --- | --- | --- |
| `DISCORD_BOT_TOKEN` | `test-token-for-ci` | Yes in real use | Discord authentication |
| `OPENROUTER_API_KEY` | `test-key-for-ci` | Yes in real use | Text-model requests |
| `RUNPOD_IO_API_KEY` | empty | For image commands | Used by draw/edit flows |
| `DEFAULT_MODEL` | `anthropic/claude-haiku-4.5` | No | Fallback text model |
| `DEFAULT_DRAW_MODEL` | `seedream` | No | Default draw model key |
| `DEFAULT_EDIT_MODEL` | `seedream` | No | Default edit model key |
| `INCLUDE_USERNAMES` | `true` | No | Include usernames in prompt context |
| `REPLY_TO_MENTIONS` | `true` | No | Enable mention replies |
| `INCLUDE_NUM_CHATLINES` | `10` | No | Recent-history lines included in context |
| `DISCORD_GUILD_ID` | empty | No | Guild-scoped sync/behavior |
| `DEATH_CHANNEL_ID` | empty | No | Target channel for deathwatch posts |
| `KEEP_UP_TO_DATE_WITH_GIT` | `false` | No | Enables periodic git polling/restart behavior |
| `QUIET_UPDATES` | `false` | No | Suppresses restart/update announcements |
| `MAX_CACHE_SIZE` | `200` | No | Persistent downloader cache size limit |
| `TRANSCRIPT_PROXY` | empty | No | Optional helper for transcript/media/article retrieval |

## Downloader/provider variables

| Variable | Required when | Notes |
| --- | --- | --- |
| `RAPIDAPI_KEY` | Using Twitter/X enrichment | Required for tweet/context fetches |
| `GROQ_API_KEY` | Using TikTok/Facebook/Twitter-video transcription | Used for audio transcription |

## MariaDB variables

These are consumed by both the Python app and the log-viewer backend.

| Variable | Default | Notes |
| --- | --- | --- |
| `DB_HOST` | `127.0.0.1` | Use `db` in Docker dev stack |
| `DB_PORT` | `3306` | Standard MariaDB port |
| `DB_USER` | `cmwgpt_user` | App DB user |
| `DB_PASSWORD` | empty | Must be set for real deployments |
| `DB_NAME` | `cmwgpt` | Database containing request/pipeline logs |

## Log-viewer backend variables

| Variable | Default | Required | Notes |
| --- | --- | --- | --- |
| `LOG_VIEWER_PORT` | `3001` via server/runtime | No | Backend listener port |
| `LOG_VIEWER_HOST` | `127.0.0.1` locally, `0.0.0.0` in Docker env | No | Backend listener host |
| `LOG_VIEWER_ALLOWED_ORIGINS` | Dev localhost origins if non-production and unset | Yes in production | Required in production; comma-separated origins |
| `JWT_SECRET` | dev-only fallback secret if unset and non-production | Yes in production | Must be set in production |
| `JWT_EXPIRES_IN` | `7d` in dev, `12h` in production | No | Token TTL |
| `LOG_VIEWER_DEV_AUTH_ENABLED` | unset/false unless configured | No | Enables dev credential auth path |
| `LOG_VIEWER_DEV_USERNAME` | empty | With dev auth | Dev-login username |
| `LOG_VIEWER_DEV_PASSWORD` | empty | With dev auth | Dev-login password |

### Backend production rules

- `JWT_SECRET` must be set in production.
- `LOG_VIEWER_ALLOWED_ORIGINS` must be set in production.
- In non-production, the backend falls back to a dev JWT secret and localhost origins if necessary.

## Log-viewer frontend variables

| Variable | Default | Notes |
| --- | --- | --- |
| `VITE_PORT` | `5173` | Vite dev server port |
| `VITE_PREVIEW_PORT` | `4173` | `vite preview` port |
| `VITE_API_URL` | `/api` | Base URL used by login, logs, details, and media helpers |
| `VITE_SOCKET_URL` | `/` | Socket.IO base URL |
| `VITE_BACKEND_PROXY_TARGET` | `http://backend:3001` | Dev proxy target used by Vite |
| `VITE_DISCORD_GUILD_ID` | fallback demo guild ID in frontend utility code | Optional UI helper value |

## Recommended minimal setups

### Minimal bot-only local setup

Fill at least:

- `DISCORD_BOT_TOKEN`
- `OPENROUTER_API_KEY`
- `DB_HOST`
- `DB_PORT`
- `DB_USER`
- `DB_PASSWORD`
- `DB_NAME`

Add these as needed:

- `RUNPOD_IO_API_KEY` for image commands
- `GROQ_API_KEY` for transcription-heavy sources
- `RAPIDAPI_KEY` for Twitter/X enrichment

### Full Docker dev stack

Use `.env.development.example` as the baseline. It already includes:

- bot variables
- MariaDB container values
- backend auth/CORS values
- frontend Vite values

## Reality checks and gotchas

- `DISCORD_CHANNEL_ID` still appears in sample env files, but it is not part of the core current runtime configuration described here.
- `make run` and restart-wrapper workflows can behave differently from `make run-direct`; see `docs/development.md`.
- If logs appear empty, verify DB config before debugging the frontend.
