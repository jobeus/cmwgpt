# Commands

This page documents the command surface that is actually present in the current codebase.

## First, the important mental model

The bot's main chat experience is **mention-based**:

- mention the bot in a normal Discord message
- the mention pipeline builds context from recent chat, attachments, embeds, and supported URLs
- on the Claude API direct models, the model also calls tools on its own (drawing, memory, Discord/IRC log search, links); see `docs/architecture.md#claude-api-tools`
- there is **no current `/chat` command**

## Command quick reference

| Command | What it does | State/persistence |
| --- | --- | --- |
| `/help` | Shows in-bot help | none |
| `/model` | Sets the text model for the current channel | persisted per channel |
| `/systemprompt set` | Stores a custom system prompt | persisted per channel |
| `/systemprompt view` | Shows the current channel system prompt | reads per-channel state |
| `/systemprompt reset` | Clears the custom system prompt | persisted per channel |
| `/draw` | Generates an image from a prompt | uses the channel's image model |
| `/edit` | Edits an uploaded image | uses the channel's image model |
| `/imagemodel` | Sets the image model for `/draw`, `/edit` and the draw tools | persisted per channel |
| `/death set` | Configures deathwatch behavior | persisted service state |
| `/death view` | Shows deathwatch settings | reads persisted state |
| `/death reset` | Restores default deathwatch settings | persisted reset |
| `/limerick` | Writes a limerick about a person (name or Wikipedia URL) | runtime action |
| `/forcedeath` | Posts a death announcement for a Wikipedia article, for testing (admin only) | runtime action |
| `/memory show` | Shows you (only you) what the bot remembers about you | reads `cmwgpt:memory:<guild>:<user>` in Redis |
| `/memory correct` | Privately has the memory model fix or add to your memory | persisted per user, per server |
| `/memory clear` | Privately forgets everything about you | persisted per user, per server |
| `/fillmemory` | Rebuilds everyone's learned memory from the last 7 days (admin only) | rewrites every memory key |
| `/botstate` | Privately shows the bot's own mood, emotion, emoji, interests and wonderings | reads `cmwgpt:self:<guild>` in Redis |
| `/wikicount` | Looks up a Wikipedia article's average monthly pageviews | runtime action |
| `/cost` | Shows what the bot has spent today, in the last 7 days and this month, by provider | reads `cmwgpt:costs:<day>` in Redis |
| `/restart` | Triggers restart flow and `git pull` | runtime action |

## Memory commands

The bot keeps a short profile of each person, per server, in Redis (`cmwgpt:memory:<guild>:<user>`).
Every night at 02:00 America/Denver, `MemoryService` reads the chat since the last run
from every text channel it can see (message text, reply targets, embed titles and
descriptions, and attachment names; no URL fetching or transcription). For each day it
makes one `MEMORY_MODEL` call to pull out new facts about each speaker, then one small call
per speaker to merge those facts into their profile. There's no length cap: the model is told
to keep every useful fact first and be succinct second. When the bot replies to a mention,
it adds the profiles of the asker, anyone they @mentioned and the
recent speakers (up to 8 people) to the end of the mention. The cached system prompt and
history stay unchanged.

All memory calls, including `/memory correct`, use medium effort on Claude API models.

Nothing is summarized per channel or per day. A mention's prompt carries the recent history
window (the last `INCLUDE_NUM_CHATLINES` messages, growing while mentions keep coming within
an hour), then the people's profiles on the mention. For anything older, the model searches
the chat with its tools.

Every `/memory` command replies only to you. `correct` sends your correction and the current
profile to `MEMORY_MODEL`, which changes, removes or adds only what the correction covers, and
then shows you the updated profile (as a private file if it's over 2000 characters). Corrections live in the same profile the nightly job
maintains, so later chat can refine them; `/fillmemory` rebuilds profiles from scratch and
drops them. Administrators can pass the optional `user` argument to `show` and `correct` to
act on someone else. Only the current profile is kept (one Redis key per person, up to 100 KB);
there is no history of earlier versions.

### `/fillmemory`

Requires the same administrator permission as `/restart`. It wipes every learned profile
in the server and rebuilds them from the last 7 days, posting progress after each day. The
nightly job then picks up from where the fill ended.

### `/botstate`

Anyone can use it; the reply is visible only to them. Shows the bot's own state in this
server as an embed: its mood, emotion (one or two words), feels emoji, interests and what it's wondering about (each with the date
it last changed). It only reads; nothing changes. The bot itself changes its state while
chatting, with the `update_self` tool, and fills in any part that's missing.

## Model and prompt commands

### `/model`

Sets the active text model for the current channel.

Choices come from `src/services/model_catalog.py`. A model only appears when its API key
is set (`GEMINI_API_KEY` or `ANTHROPIC_API_KEY`), and a channel set to a model whose key is
missing, or to a model that no longer exists (such as the old OpenRouter ones), falls back to
`DEFAULT_MODEL`.

| Model | Notes |
| --- | --- |
| `sonnet-5.5-direct` | `claude-sonnet-5-5` straight from the Claude API (search / web aware, needs `ANTHROPIC_API_KEY`) |
| `opus-5.5-direct` | `claude-opus-5-5` straight from the Claude API (search / web aware, needs `ANTHROPIC_API_KEY`; 2x Sonnet's price) |
| `google` | native Gemini (`gemini-3.8-flash`, search) |
| `google-high` | native Gemini (`gemini-3.8-flash`, search, thinking) |

`google`/`google-high` route through `GeminiService` and `sonnet-5.5-direct`/`opus-5.5-direct`
through `AnthropicService`. The routing itself lives in `src/services/completion_dispatch.py`.

### `/systemprompt ...`

These commands control the per-channel system prompt layered on top of the default prompt file.

| Subcommand | Meaning |
| --- | --- |
| `set` | Save a custom system prompt for this channel |
| `view` | Display the active prompt for this channel |
| `reset` | Remove the custom prompt and fall back to the default prompt file |

## Image commands

These use Gemini's image models (`src/services/image_service.py`, `GeminiService.generate_image`)
and need `GEMINI_API_KEY`.

| Command | Purpose |
| --- | --- |
| `/draw` | Generate an image from a text prompt, optionally with reference images |
| `/edit` | Edit an uploaded image (the first attachment) using a prompt; further attachments are references |
| `/imagemodel` | View or set the channel's image model, used by `/draw`, `/edit` and the chat model's `draw`/`edit_image` tools |

| Model | ID | Aspect ratios | Sizes | Thinking | Search |
| --- | --- | --- | --- | --- | --- |
| `banana-2.1` (default) | `gemini-nano-banana-2.1` | standard + `1:4`, `4:1`, `1:8`, `8:1` | 1K, 2K, 4K | minimal, medium, high | web, web+image |
| `banana-pro` | `gemini-3-pro-image` | standard | 1K, 2K, 4K | always on, not settable | web |
| `banana-2-lite` | `gemini-3.1-flash-lite-image` | standard | 1K | minimal, high | none |

The standard aspect ratios are `1:1`, `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9` and
`21:9`. `/draw` and `/edit` take every option as an optional argument (`aspect_ratio`, `size`,
`model`, `thinking`, `search`) plus up to 8 images; an option the chosen model doesn't support is
refused with a message naming what it takes. `/draw` defaults to 16:9; `/edit` keeps the edited
image's shape. Any text the model writes with the image is posted under it. The `draw` and
`edit_image` tools take the same options (and up to 14 images for `edit_image`), so the chat model
can, for example, draw a 9:16 portrait.

### `/cost`

Shows what the bot has spent today, over the last 7 days and so far this month
(days run on America/Denver time), each broken down by provider: `claude-direct`,
`gemini-direct` and `groq` (older days may also show `openrouter` and `runpod`, from before those
were removed). Providers that cost nothing in a period are left out. It ends with this month's top spenders, named without pinging them.

Every paid call reports its cost through `record_cost` in `src/services/cost_service.py`,
in the service that made the call:

| Provider | Where the cost comes from |
| --- | --- |
| `claude-direct` | Token usage and web searches priced at each model's rates, per request (including advisor calls and tool rounds) |
| `gemini-direct` | Token usage priced at Gemini's rates, for chat and for image models (image output tokens at the image rate; search grounding isn't priced, as the first 5,000 a month are free) |
| `groq` | The audio's duration at Whisper's per-hour price (at least 10 seconds per request) |

Costs are tallied per day in a Redis hash, `cmwgpt:costs:<YYYY-MM-DD>`, with one field per
provider, per person (`user:<id>`) and per channel (`channel:<id>`). They're kept about
400 days. A mention's costs are charged to whoever mentioned the bot, and a slash command's
to whoever ran it. Background work (the nightly memory job and
deathwatch posts) counts toward its provider only.

A reply's `[$0.012]` prefix is the total of everything recorded while answering it: the
model calls and whatever its tools spent (draws and edits, transcripts of
linked videos and voice notes, memory edits).

## Persistence model

Most command-driven settings are persisted through `StateService`, which means model choices, prompt overrides, and related service settings survive ordinary runtime operations.

## Where these commands are defined

| Path | Responsibility |
| --- | --- |
| `src/bot/commands/system.py` | `/help`, `/model`, `/systemprompt`, `/restart` |
| `src/bot/commands/image.py` | `/draw`, `/edit`, `/imagemodel` |
| `src/bot/commands/death.py` | `/death ...` |
| `src/bot/commands/wikicount.py` | `/wikicount` |
| `src/bot/commands/cost.py` | `/cost` |
