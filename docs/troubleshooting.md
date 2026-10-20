# Troubleshooting

Use this page like a triage board: start with the symptom, then work from the shortest path to the root cause.

## Fast symptom index

| Symptom | Check first |
| --- | --- |
| Bot does not answer mentions | token, permissions, `REPLY_TO_MENTIONS`, whether the bot was actually mentioned |
| Slash commands are missing/stale | `DISCORD_GUILD_ID`, guild membership, whether the bot restarted |
| URL enrichment is missing | source-specific keys like `RAPIDAPI_KEY` / `GROQ_API_KEY` / `TRANSCRIPT_PROXY` |
| `/memory` fails or mentions carry no user notes | DB connectivity, schema application, `MEMORY_MODEL` API key |
| Restart behavior is confusing | `/restart` semantics, exit code `42`, wrapper vs direct run |
| Tests fail unexpectedly | dependency install state and targeted reruns |

## Bot does not reply to mentions

### Quick checks

| Check | Why |
| --- | --- |
| `DISCORD_BOT_TOKEN` is valid | Bot may not be connected correctly |
| Bot has read/send permissions in the channel | Mention can be seen but reply may fail |
| `REPLY_TO_MENTIONS=true` | Mention handling can be disabled by config |
| The message actually mentions the bot | The main chat path is mention-based |

If slash commands work but mention replies do not, focus on the **mention pipeline** rather than command registration.

## Slash commands are missing or stale

### Most likely causes

| Cause | What to verify |
| --- | --- |
| Wrong guild scope | `DISCORD_GUILD_ID` matches the intended guild |
| Bot is in the wrong place | The bot is present in the expected guild |
| Commands changed but process did not refresh | Restart the bot and let command sync happen |

Reference: `docs/commands.md`

## URL enrichment is missing

The downloader pipeline is **provider-specific**, so diagnose by source rather than treating it as one generic failure.

### Provider matrix

| Source | What it depends on | What to check |
| --- | --- | --- |
| YouTube | `youtube_transcript_api`, optional proxy | If transcripts are blocked, try `TRANSCRIPT_PROXY` |
| TikTok | `yt-dlp`, ffmpeg, Groq transcription | `GROQ_API_KEY`, media extraction path, proxy fallback |
| Twitter/X | RapidAPI, optional Groq for embedded video | `RAPIDAPI_KEY`, then `GROQ_API_KEY` if video transcription is needed |
| Facebook | `yt-dlp`, ffmpeg, Groq transcription | `GROQ_API_KEY`, media download path, optional proxy behavior |
| Articles | `fetch_public` (`httpx`), `trafilatura` (with a `favor_recall` retry) | URL reachability and extraction success |

### Good debugging habit

If one provider fails while others work, assume a **provider-specific dependency/config problem**, not a global mention or model issue.

### Limits that can make a link look "missing"

| Limit | Effect |
| --- | --- |
| SSRF guard | URLs (or any redirect hop) that resolve to a non-global address (loopback, private, link-local, CGNAT, reserved, ...) are refused |
| Size cap / overall timeout | Very large or slow pages and images are cut off by `src/utils/http_client.py` and skipped (videos have no size cap) |
| Video download time | A video download (direct or yt-dlp) that takes over 3 minutes is abandoned. Videos of any length are otherwise transcribed in full; Groq itself is near-instant |
| History window | Only the newest 10 history messages fetch links; older messages use cached results only, so an uncached link further back adds nothing |

The cause is logged at warning level in the normal bot log.

## Database/memory problems

### Common causes

| Problem | Check |
| --- | --- |
| Connection refused / timeout | `DB_HOST`, `DB_PORT`, service/container health |
| Access denied | `DB_USER`, `DB_PASSWORD`, grants |
| Wrong schema/database | `DB_NAME` and whether `init_db.sql` was applied |
| Profiles never update | `MEMORY_MODEL` has its API key; the nightly job is off without it |

MariaDB is only used for per-user memory, so the bot keeps replying when it is down. If `/memory` fails or mentions never include user notes, verify MariaDB first.

Reference: `docs/mariadb_setup.md`

## Restart/update behavior is confusing

### Keep these facts in your head

| Fact | Meaning |
| --- | --- |
| `/restart` performs a `git pull` | It is not just a process restart |
| Restart runs the normal shutdown, then exits with code `42` | A supervisor (or `start.sh`) must restart the process |
| `QUIET_UPDATES` only affects announcements | It does not disable the restart mechanism |

For local development, `make run-direct` is usually easier to reason about than the wrapper-based run path.

Reference: `docs/auto-update.md`

## `make run` behaves oddly after restart

`start.sh` only restarts the bot when it exits with code `42`, and it reinstalls `requirements.txt` before each restart. Any other exit code (including a crash) ends the loop, which is why hosted runs should sit under systemd (`deploy/cmwgpt.service`).

If wrapper-driven restarts behave strangely:

- check that `.venv/` (or a legacy `venv/`) exists, since the wrapper refuses to start without one
- look at the shutdown lines in the log: the bot waits up to 90 seconds for queued replies before exiting
- use `make run-direct` to rule the wrapper out

## Tests fail unexpectedly

### First fix to try

```bash
make install
make install-test
make test-verbose
```

### Better iteration pattern

If the full suite is noisy, run the smallest relevant target first, then expand outward only if needed.

Reference: `tests/README.md`
