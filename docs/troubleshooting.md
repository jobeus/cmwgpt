# Troubleshooting

Use this page like a triage board: start with the symptom, then work from the shortest path to the root cause.

## Fast symptom index

| Symptom | Check first |
| --- | --- |
| Bot does not answer mentions | token, permissions, `REPLY_TO_MENTIONS`, whether the bot was actually mentioned |
| Slash commands are missing/stale | `DISCORD_GUILD_ID`, guild membership, whether the bot restarted |
| URL enrichment is missing | source-specific keys like `RAPIDAPI_KEY` / `GROQ_API_KEY` / `TRANSCRIPT_PROXY` |
| Logs are missing | DB connectivity, schema application, backend access to MariaDB |
| Log viewer login fails | auth mode, JWT config, dev auth credentials, PAM availability |
| Frontend cannot reach backend | `VITE_API_URL`, `VITE_SOCKET_URL`, `VITE_BACKEND_PROXY_TARGET`, backend port/host |
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
| Articles | `httpx`, `trafilatura`, `newspaper3k` fallback | URL reachability and extraction success |

### Good debugging habit

If one provider fails while others work, assume a **provider-specific dependency/config problem**, not a global mention or model issue.

## Database/logging problems

### Common causes

| Problem | Check |
| --- | --- |
| Connection refused / timeout | `DB_HOST`, `DB_PORT`, service/container health |
| Access denied | `DB_USER`, `DB_PASSWORD`, grants |
| Wrong schema/database | `DB_NAME` and whether `init_db.sql` was applied |
| Log viewer is empty | Backend DB access and schema presence before frontend debugging |

If the bot otherwise runs but logs are missing or the viewer is empty, verify MariaDB first.

Reference: `docs/mariadb_setup.md`

## Log viewer login problems

The backend supports two auth modes:

| Mode | Used when | What matters |
| --- | --- | --- |
| Development credential auth | `LOG_VIEWER_DEV_AUTH_ENABLED=true` | `LOG_VIEWER_DEV_USERNAME` and `LOG_VIEWER_DEV_PASSWORD` must be set |
| PAM auth | Dev auth is disabled | The backend host environment must support PAM auth for the provided OS user |

### Things to verify

| Setting | Why |
| --- | --- |
| `JWT_SECRET` | Token signing/verification |
| `LOG_VIEWER_ALLOWED_ORIGINS` | Browser access and CORS |
| `LOG_VIEWER_DEV_AUTH_ENABLED` | Determines dev-auth path |
| `LOG_VIEWER_DEV_USERNAME` / `LOG_VIEWER_DEV_PASSWORD` | Required if dev auth is enabled |

If development auth is enabled but credentials are missing, login will fail due to backend misconfiguration.

## Frontend cannot reach backend

### First checks

| Variable | Purpose |
| --- | --- |
| `VITE_API_URL` | HTTP base URL for login/log endpoints |
| `VITE_SOCKET_URL` | Socket.IO connection target |
| `VITE_BACKEND_PROXY_TARGET` | Vite proxy destination in dev |

The default Vite setup expects:

- `/api` to proxy to the backend
- `/socket.io` to proxy to the backend websocket endpoint

Also confirm the backend is actually listening on the expected host/port.

## Restart/update behavior is confusing

### Keep these facts in your head

| Fact | Meaning |
| --- | --- |
| `/restart` performs a `git pull` | It is not just a process restart |
| Restart exits with code `42` | A supervisor must restart the process |
| `QUIET_UPDATES` only affects announcements | It does not disable the restart mechanism |

For local development, `make run-direct` is usually easier to reason about than the wrapper-based run path.

Reference: `docs/auto-update.md`

## `make run` behaves oddly after restart

The key mismatch is:

| Component | Expected virtualenv path |
| --- | --- |
| `Makefile` | `.venv/` |
| `start.sh` restart wrapper | `venv/` |

If wrapper-driven restarts behave strangely, do one of these:

- use `make run-direct`
- create the path the wrapper expects
- update the wrapper for your machine/workflow

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
