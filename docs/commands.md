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
| `/restart` | Triggers restart flow and `git pull` | runtime action |

## Model and prompt commands

### `/model`

Sets the active text model for the current channel.

Current choices exposed in code:

| Model |
| --- |
| `anthropic/claude-haiku-4.5` |
| `google/gemini-2.5-flash` |
| `openai/gpt-5-mini` |

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

Current image model keys exposed by the bot include:

| Model key |
| --- |
| `seedream` |
| `qwen-image` |
| `pruna` |
| `wan-2.6` |
| `flux-kontext` |
| `z-image-edit` |

## Interject commands

The interject feature lets the bot occasionally speak without being directly mentioned when a channel is active enough.

### `/interject set`

Parameters currently exposed in code:

| Parameter | Meaning |
| --- | --- |
| `chance_percent` | Probability of interjection when conditions are met |
| `cooldown_minutes` | Minimum time between interjections |
| `min_messages` | Minimum message activity before interjection can happen |
| `activity_window_minutes` | Lookback window for channel activity |
| `context_lines` | How much recent context to include |
| `max_daily` | Daily cap on interjections |
| `exclude_embeds` | Whether embed-only context should be ignored |

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
| `interval_seconds` | How often to run checks |
| `min_avg_monthly_views` | Minimum average pageviews threshold |
| `pageview_months` | Number of months considered |

### Other death subcommands

| Subcommand | Meaning |
| --- | --- |
| `view` | Show current settings |
| `reset` | Restore defaults |

## Persistence model

Most command-driven settings are persisted through `StateService`, which means model choices, prompt overrides, and related service settings survive ordinary runtime operations.

## Where these commands are defined

| Path | Responsibility |
| --- | --- |
| `src/bot/commands/system.py` | `/help`, `/model`, `/systemprompt`, `/restart` |
| `src/bot/commands/image.py` | `/draw`, `/drawmodel`, `/edit`, `/editmodel` |
| `src/bot/commands/interject.py` | `/interject ...` |
| `src/bot/commands/death.py` | `/death ...` |
