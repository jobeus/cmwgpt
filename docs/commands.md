# Commands

This page documents the command surface that is actually present in the current codebase.

## First, the important mental model

The bot's main chat experience is **mention-based**:

- mention the bot in a normal Discord message
- the mention pipeline builds context from recent chat, attachments, embeds, and supported URLs
- there is **no current `/chat` command**

## Command quick reference

| Command | What it does | State/persistence |
| --- | --- | --- |
| `/help` | Shows in-bot help | none |
| `/model` | Sets the text model for the current channel | persisted per channel |
| `/systemprompt set` | Stores a custom system prompt | persisted per channel |
| `/systemprompt view` | Shows the current channel system prompt | reads per-channel state |
| `/systemprompt reset` | Clears the custom system prompt | persisted per channel |
| `/draw` | Generates an image from a prompt | uses current draw model |
| `/drawmodel` | Sets the draw model | persisted per channel |
| `/edit` | Edits an uploaded image | uses current edit model |
| `/editmodel` | Sets the edit model | persisted per channel |
| `/death set` | Configures deathwatch behavior | persisted service state |
| `/death view` | Shows deathwatch settings | reads persisted state |
| `/death reset` | Restores default deathwatch settings | persisted reset |
| `/memory show` | Shows you (only you) what the bot remembers about you | reads `user_memory` |
| `/memory correct` | Privately has the memory model fix or add to your memory | persisted per user, per server |
| `/memory clear` | Privately forgets everything about you | persisted per user, per server |
| `/fillmemory` | Rebuilds everyone's learned memory from the last 7 days (admin only) | rewrites `user_memory` |
| `/wikicount` | Looks up a Wikipedia article's average monthly pageviews | runtime action |
| `/restart` | Triggers restart flow and `git pull` | runtime action |

## Memory commands

The bot keeps a short profile of each person, per server, in MariaDB (`user_memory`).
Every night at 02:00 America/Denver, `MemoryService` reads the chat since the last run
from every text channel it can see (message text, reply targets, embed titles and
descriptions, and attachment names; no URL fetching or transcription). For each day it
makes one `MEMORY_MODEL` call to pull out new facts about each speaker, then one small call
per speaker to merge those facts into their profile. There's no length cap: the model is told
to keep every useful fact first and be succinct second. When the bot replies to a mention,
it adds the profiles of the asker, anyone they @mentioned and the
recent speakers (up to 8 people) to the end of the mention. The cached system prompt and
history stay unchanged.

Every `/memory` command replies only to you. `correct` sends your correction and the current
profile to `MEMORY_MODEL`, which changes, removes or adds only what the correction covers, and
then shows you the updated profile (as a paste.rs link if it's over 2000 characters). Corrections live in the same profile the nightly job
maintains, so later chat can refine them; `/fillmemory` rebuilds profiles from scratch and
drops them. Administrators can pass the optional `user` argument to `show` and `correct` to
act on someone else. Every change also writes a row to
`user_memory_history`, so you can see how a profile changed and restore an earlier version.

### `/fillmemory`

Requires the same administrator permission as `/restart`. It wipes every learned profile
in the server and rebuilds them from the last 7 days, posting progress after each day. The
nightly job then picks up from where the fill ended.

## Model and prompt commands

### `/model`

Sets the active text model for the current channel.

Choices come from `src/services/model_catalog.py`. A model only appears when its API key
is set (`OPENROUTER_API_KEY`, `GEMINI_API_KEY`, or `ANTHROPIC_API_KEY`), and a channel set to
a model whose key is missing falls back to `DEFAULT_MODEL`.

| Model | Notes |
| --- | --- |
| `google/gemini-2.5-flash` | via OpenRouter |
| `bytedance-seed/seed-2.0-mini` | via OpenRouter |
| `bytedance-seed/seed-1.6-flash` | via OpenRouter |
| `qwen/qwen3.5-flash-02-23` | via OpenRouter |
| `anthropic/claude-sonnet-5.5` | search / web aware (via OpenRouter) |
| `sonnet-5.5-direct` | `claude-sonnet-5-5` straight from the Claude API (search / web aware, needs `ANTHROPIC_API_KEY`) |
| `opus-5.5-direct` | `claude-opus-5-5` straight from the Claude API (search / web aware, needs `ANTHROPIC_API_KEY`; 2x Sonnet's price) |
| `google` | native Gemini (`gemini-3.8-flash`, search) |
| `google-high` | native Gemini (`gemini-3.8-flash`, search, thinking) |

`google`/`google-high` route through `GeminiService`, `sonnet-5.5-direct`/`opus-5.5-direct` route through
`AnthropicService`, and the rest route through `OpenAIService` → OpenRouter. The
routing itself lives in `src/services/completion_dispatch.py`.

### `/systemprompt ...`

These commands control the per-channel system prompt layered on top of the default prompt file.

| Subcommand | Meaning |
| --- | --- |
| `set` | Save a custom system prompt for this channel |
| `view` | Display the active prompt for this channel |
| `reset` | Remove the custom prompt and fall back to the default prompt file |

## Image commands

These commands use `RunpodService` and channel-level state for model selection.

| Command | Purpose |
| --- | --- |
| `/draw` | Generate an image from a text prompt |
| `/drawmodel` | Set the model used by `/draw` |
| `/edit` | Edit an uploaded image using a prompt |
| `/editmodel` | Set the model used by `/edit` |

Current image model keys exposed by the bot (the non-`seedream` keys appear only
when Runpod models are enabled):

| Command | Model keys |
| --- | --- |
| `/draw`, `/drawmodel` | `seedream`, `z-image`, `wan-2.6`, `pruna`, `qwen`, `flux` |
| `/edit`, `/editmodel` | `seedream`, `qwen`, `pruna` |


## Death commands

The deathwatch feature periodically checks guild members against external death-signal heuristics and can post notices into `DEATH_CHANNEL_ID`.

### `/death set`

Current parameters:

| Parameter | Meaning |
| --- | --- |
| `poll_interval` | Polling interval in seconds (default 15) |
| `min_views` | Minimum average monthly views to announce (default 180000) |
| `pageview_months` | How many months to average pageview data (default 12) |

### Other death subcommands

| Subcommand | Meaning |
| --- | --- |
| `view` | Show current settings |
| `reset` | Restore defaults |

## Wikicount command

### `/wikicount`

Looks up a Wikipedia article's average monthly pageviews — the same heuristic the
deathwatch feature uses — for ad-hoc queries.

| Parameter | Meaning |
| --- | --- |
| `target` | Wikipedia URL or exact article title (e.g. `Daveigh Chase` or `Las Vegas`) |

## Persistence model

Most command-driven settings are persisted through `StateService`, which means model choices, prompt overrides, and related service settings survive ordinary runtime operations.

## Where these commands are defined

| Path | Responsibility |
| --- | --- |
| `src/bot/commands/system.py` | `/help`, `/model`, `/systemprompt`, `/restart` |
| `src/bot/commands/image.py` | `/draw`, `/drawmodel`, `/edit`, `/editmodel` |
| `src/bot/commands/death.py` | `/death ...` |
| `src/bot/commands/wikicount.py` | `/wikicount` |
