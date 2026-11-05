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
| `GEMINI_API_KEY` | empty | For `google`/`google-high` and image commands | Native Gemini (`GeminiService`) requests and image generation |
| `ANTHROPIC_API_KEY` | empty | Yes in real use (default model) | Claude API (`AnthropicService`) requests |
| `GEMINI_TIMEOUT_SECONDS` | `120` | No | Client-side request timeout for Gemini calls |
| `DEFAULT_MODEL` | `sonnet-5.5-direct` | No | Fallback text model (one of the `/model` choices) |
| `MEMORY_MODEL` | `sonnet-5.5-direct` | No | Model the nightly user-memory job uses; the job is off when this model's API key is missing |
| `DEFAULT_IMAGE_MODEL` | `gemini-nano-banana-2.1` | No | Image model for `/draw`, `/edit` and the draw tools when a channel hasn't set one with `/imagemodel` |
| `REPLY_TO_MENTIONS` | `true` | No | Enable mention replies |
| `INCLUDE_NUM_CHATLINES` | `10` | No | Recent-history lines included in context |
| `DISCORD_GUILD_ID` | empty | No | Guild-scoped sync/behavior |
| `DEATH_CHANNEL_ID` | empty | No | Target channel for deathwatch posts |
| `IRC_LOG` | empty | No | Path to the channel's old irssi IRC log; enables the direct models' `search_irc_log`/`read_irc_log_day`/`count_irc_log` tools |
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

## Tracing (Pydantic Logfire)

| Variable | Default | Notes |
| --- | --- | --- |
| `LOGFIRE_TOKEN` | empty | Logfire project write token (`LOGFIRE_API_KEY` is read too). Set it and the bot sends traces and INFO+ logs; leave it empty and nothing is sent |
| `LOGFIRE_READ_TOKEN` | the tracing key | A Logfire key with `project:read`, for the logs tool below. Only needed when the tracing key can't read |
| `LOGFIRE_MCP_URL` | `https://logfire-us.pydantic.dev/mcp` | Logfire's MCP server (`logfire-eu` for an EU project) |
| `DISCORD_CHANNEL_ID` | empty | The one channel where mentions get the logs tool below; unset, it's off everywhere |
| `LOGFIRE_ENVIRONMENT` | `development` for `.env.development`, else `production` | The environment Logfire files the traces under |

Each mention is one trace: the context building, every Claude/Gemini call (prompt, reply, tool calls, tokens), every tool call, HTTP fetches and Redis commands, with the mention's cost and usage stats on the root span. Slash commands, the nightly memory job, death announcements and delivered reminders get their own traces. Images in prompts and tool results show up as a `[image/png, 412 KB: https://paste.jobe.wtf/abc.png]` note instead of base64: PNGs and JPEGs are uploaded to paste.jobe.wtf (once each; the links are kept in Redis) so the trace links to them. Gemini chat calls don't record their images at all. The 15-second reminder and death-page polls only keep a trace when the poll does something. The code is in `src/telemetry.py`.

The direct Claude models can also read these traces when answering a mention in `DISCORD_CHANNEL_ID`, so you can ask the bot there why it failed or was slow. Only that channel gets it (a thread under it has its own id and doesn't), because the traces hold every channel's conversations. Each mention request includes Logfire's MCP server through the Claude API's MCP connector: Anthropic connects to it with the key from the request, and nothing is stored on the Claude side. Only read-only tools are allowed (`query_run`, `query_schema_reference`, `query_find_exceptions_in_file`, `issue_list`). If Logfire rejects the key or is down, the request is retried without the logs. Anyone who can post in that channel can ask it about any traced conversation.

## Recommended minimal setups

### Minimal bot-only local setup

Fill at least:

- `DISCORD_BOT_TOKEN`
- `ANTHROPIC_API_KEY`

Add these as needed:

- `GEMINI_API_KEY` for the `google`/`google-high` text models and image commands
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
