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
| `/interject set` | Configures bot interjection behavior | persisted per channel/service state |
| `/interject view` | Shows interjection settings | reads persisted state |
| `/interject reset` | Restores default interjection settings | persisted reset |
| `/interject count` | Shows current interjection counters/status | runtime/service state |
| `/death set` | Configures deathwatch behavior | persisted service state |
| `/death view` | Shows deathwatch settings | reads persisted state |
| `/death reset` | Restores default deathwatch settings | persisted reset |
| `/wikicount` | Looks up a Wikipedia article's average monthly pageviews | runtime action |
| `/restart` | Triggers restart flow and `git pull` | runtime action |

## Model and prompt commands

### `/model`

Sets the active text model for the current channel.

Current choices exposed in code:

| Model | Notes |
| --- | --- |
| `google/gemini-2.5-flash` | via OpenRouter |
| `bytedance-seed/seed-2.0-mini` | via OpenRouter |
| `bytedance-seed/seed-1.6-flash` | via OpenRouter |
| `qwen/qwen3.5-flash-02-23` | via OpenRouter |
| `anthropic/claude-haiku-4.5` | search / web aware (via OpenRouter) |
| `google` | native Gemini (`gemini-3.1-flash-lite`, search) |
| `google-high` | native Gemini (`gemini-3.1-flash-lite`, search, thinking) |
| `hybrid` | two-phase: `google-high` gathers context, then `claude-haiku-4.5` writes the reply |

`google`/`google-high`/`hybrid` route through `GeminiService` (and, for `hybrid`,
also `OpenAIService`); the rest route through `OpenAIService` → OpenRouter. The
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

## Interject commands

The interject feature lets the bot occasionally speak without being directly mentioned when a channel is active enough.

### `/interject set`

Parameters currently exposed in code:

| Parameter | Meaning |
| --- | --- |
| `chance` | Percentage chance (0-100) to interject when conditions are met |
| `cooldown` | Per-channel cooldown in minutes after an interjection or failed roll |
| `min_messages` | Minimum qualifying messages in the activity window to trigger |
| `min_authors` | Minimum number of distinct non-bot authors in the qualifying streak |
| `window_mins` | Only messages within this many minutes from now count |
| `context_lines` | How many recent messages to include as AI context |
| `daily_max` | Daily cap on interjections per channel |
| `exclude_embeds` | Whether messages with embeds/attachments break the streak |

### Other interject subcommands

| Subcommand | Meaning |
| --- | --- |
| `view` | Show current settings |
| `reset` | Restore defaults |
| `count` | Show runtime counters/status |

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
| `src/bot/commands/interject.py` | `/interject ...` |
| `src/bot/commands/death.py` | `/death ...` |
| `src/bot/commands/wikicount.py` | `/wikicount` |
