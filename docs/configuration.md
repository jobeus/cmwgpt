# Configuration reference

This page focuses on the env vars that matter in the **current** runtime: the Python bot and the Redis instance that stores per-user memory and content caches.

## Which env file should you edit?

| Workflow | Recommended file |
| --- | --- |
| Local bot-only development | `.env` (usually copied from `env.example`) |
| Docker dev stack | `.env.development` (copied from `.env.development.example`) |

## Bot/runtime variables

These are loaded by `src/config.py`.

| Variable | Default | Required | Notes |
| --- | --- | --- | --- |
| `DISCORD_BOT_TOKEN` | `test-token-for-ci` | Yes in real use | Discord authentication |
| `OPENROUTER_API_KEY` | `test-key-for-ci` | Yes in real use | Text-model requests via OpenRouter |
| `GEMINI_API_KEY` | empty | For `google`/`google-high` models | Native Gemini (`GeminiService`) requests |
| `ANTHROPIC_API_KEY` | empty | For `sonnet-5.5-direct` / `opus-5.5-direct` | Direct Claude API (`AnthropicService`) requests, bypassing OpenRouter |
| `GEMINI_TIMEOUT_SECONDS` | `120` | No | Client-side request timeout for Gemini calls |
| `RUNPOD_IO_API_KEY` | empty | For image commands | Used by draw/edit flows |
| `DEFAULT_MODEL` | `anthropic/claude-sonnet-5.5` | No | Fallback text model |
| `MEMORY_MODEL` | `sonnet-5.5-direct` | No | Model the nightly user-memory job uses; the job is off when this model's API key is missing |
| `DEFAULT_DRAW_MODEL` | `seedream` | No | Default draw model key |
| `DEFAULT_EDIT_MODEL` | `seedream` | No | Default edit model key |
| `REPLY_TO_MENTIONS` | `true` | No | Enable mention replies |
| `INCLUDE_NUM_CHATLINES` | `10` | No | Recent-history lines included in context |
| `DISCORD_GUILD_ID` | empty | No | Guild-scoped sync/behavior |
| `DEATH_CHANNEL_ID` | empty | No | Target channel for deathwatch posts |
| `DISCORD_CHANNEL_ID` | empty | No | The main chat channel: the only place the bot checks in on follow-ups unprompted after an hour of quiet. Check-ins are off when empty |
| `IRC_LOG` | empty | No | Path to the channel's old irssi IRC log; enables the direct models' `search_irc_log`/`read_irc_log_day` tools |
| `IRC_NICKS` | empty | No | irssistats-style nicks file (`canonical regex` per line, first match wins, `<NULL>` drops a nick) that merges aliases in the IRC log tools' nick filters and counts |
| `OLD_QUOTE_BOT` | empty | No | Path to the old IRC quote bot's quotes (one per line); enables the direct models' `old_quotes` tool |
| `DEPLOY_WEBHOOK_SECRET` | empty | No | Shared secret for the signed deploy webhook; the listener is off when empty (see `docs/auto-update.md`) |
| `DEPLOY_WEBHOOK_HOST` | `0.0.0.0` | No | Interface the deploy webhook binds to |
| `DEPLOY_WEBHOOK_PORT` | `8787` | No | Port the deploy webhook listens on (`POST /deploy`) |
| `QUIET_UPDATES` | `false` | No | Suppresses restart/update announcements |
| `TRANSCRIPT_PROXY` | empty | No | Optional helper for transcript/media/article retrieval |

## Downloader/provider variables

| Variable | Required when | Notes |
| --- | --- | --- |
| `RAPIDAPI_KEY` | Using Twitter/X, Instagram or Reddit enrichment | Required for tweet, Instagram post and Reddit post fetches |
| `GROQ_API_KEY` | Using TikTok/Facebook/Twitter-video transcription | Used for audio transcription |

## Redis

| Variable | Default | Notes |
| --- | --- | --- |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Per-user memory and content caches; use `redis://redis:6379/0` in the Docker dev stack. See `docs/redis_setup.md` |

## Recommended minimal setups

### Minimal bot-only local setup

Fill at least:

- `DISCORD_BOT_TOKEN`
- `OPENROUTER_API_KEY`

Add these as needed:

- `GEMINI_API_KEY` for the `google`/`google-high` text models
- `ANTHROPIC_API_KEY` for the `sonnet-5.5-direct` / `opus-5.5-direct` text models
- `RUNPOD_IO_API_KEY` for image commands
- `GROQ_API_KEY` for transcription-heavy sources
- `RAPIDAPI_KEY` for Twitter/X, Instagram and Reddit enrichment
- `REDIS_URL` for per-user memory and persistent caches (the bot runs without Redis, but `/memory` and the nightly job will not work and caches only last until restart)

### Full Docker dev stack

Use `.env.development.example` as the baseline. It already includes:

- bot variables
- the Redis container URL

## Reality checks and gotchas

- `make run` and restart-wrapper workflows can behave differently from `make run-direct`; see `docs/development.md`.
- If `/memory` fails or mentions never include profiles, check `redis-cli ping` and `REDIS_URL` (see `docs/redis_setup.md`).
