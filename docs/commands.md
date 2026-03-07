# Commands

This document reflects the slash commands currently registered by the bot.

## Primary chat path

The main conversation path is **mention-based**.

- Mention the bot in a message to get a reply.
- There is **no current `/chat` slash command** in the codebase.

## System commands

### `/help`

Shows the in-bot help summary.

### `/model`

Sets the per-channel text model.

Current model choices exposed in code:

- `anthropic/claude-haiku-4.5`
- `google/gemini-2.5-flash`
- `openai/gpt-5-mini`

### `/systemprompt set`

Stores a custom per-channel system prompt.

### `/systemprompt view`

Shows the current channel system prompt.

### `/systemprompt reset`

Clears the channel-specific system prompt and returns to the default prompt.

### `/restart`

Triggers the restart flow, which saves state and performs a `git pull` before the bot exits.

## Image commands

### `/draw`

Generates an image from a prompt using the currently selected draw model.

### `/drawmodel`

Sets the draw model for the current channel.

### `/edit`

Edits an uploaded image using the current edit model.

### `/editmodel`

Sets the edit model for the current channel.

Current image model keys exposed by the bot include:

- `seedream`
- `qwen-image`
- `pruna`
- `wan-2.6`
- `flux-kontext`
- `z-image-edit`

Exact support depends on the request type and `RunpodService` mapping.

## Interject commands

The interject feature lets the bot occasionally speak on its own in active channels.

### `/interject set`

Configures interject behavior. Current parameters map to the persisted settings used by `InterjectService`:

- `chance_percent`
- `cooldown_minutes`
- `min_messages`
- `activity_window_minutes`
- `context_lines`
- `max_daily`
- `exclude_embeds`

### `/interject view`

Shows current interject settings.

### `/interject reset`

Restores default interject settings.

### `/interject count`

Shows the service's current daily status/counters.

## Death commands

The death feature periodically checks the guild member list against Wikipedia/Wikidata signals and posts matches into the configured death channel.

### `/death set`

Configures deathwatch behavior. Current parameters are:

- `interval_seconds`
- `min_avg_monthly_views`
- `pageview_months`

### `/death view`

Shows current deathwatch settings.

### `/death reset`

Restores default deathwatch settings.

## Notes on scope

- Command choices are registered from the Python code in `src/bot/commands/`.
- Mention replies remain the main way users chat with the bot.
- Per-channel settings are persisted through `StateService`.
