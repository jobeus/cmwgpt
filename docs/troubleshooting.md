# Troubleshooting

## Bot does not reply to mentions

Check:

- `DISCORD_BOT_TOKEN` is valid
- the bot has permission to read/send messages in the channel
- `REPLY_TO_MENTIONS=true`
- the bot was actually mentioned in the message

If slash commands work but mentions do not, focus on permissions and the mention pipeline rather than command registration.

## Slash commands are missing or stale

Check:

- `DISCORD_GUILD_ID` is correct for guild-scoped sync behavior
- the bot is in the expected guild
- the process restarted after command changes

Current command set is documented in `docs/commands.md`.

## URL enrichment is missing

The downloader pipeline is source-specific.

### YouTube

- transcripts come from `youtube_transcript_api`
- if YouTube access is blocked, try `TRANSCRIPT_PROXY`

### TikTok

- requires `GROQ_API_KEY`
- uses `yt-dlp` + ffmpeg to extract audio
- falls back to `TRANSCRIPT_PROXY` if direct download fails

### Twitter/X

- requires `RAPIDAPI_KEY` for the main tweet/context fetch
- uses `GROQ_API_KEY` only when an embedded video is present and needs transcription

### Facebook

- requires `GROQ_API_KEY`
- uses `yt-dlp` + ffmpeg and may fall back to `TRANSCRIPT_PROXY`

### Generic articles

- fetched with `httpx`
- extracted with `trafilatura`, then `newspaper3k` fallback

## Database errors

Common causes:

- wrong `DB_HOST`/`DB_PORT`
- wrong `DB_USER`/`DB_PASSWORD`
- missing `DB_NAME`
- `init_db.sql` not applied

If the bot otherwise runs but log viewer is empty, verify MariaDB connectivity first.

## Log viewer login problems

Check backend config:

- `JWT_SECRET`
- `LOG_VIEWER_ALLOWED_ORIGINS`
- `LOG_VIEWER_DEV_AUTH_ENABLED`
- `LOG_VIEWER_DEV_USERNAME`
- `LOG_VIEWER_DEV_PASSWORD`

In development, the Docker env example enables dev auth for convenience.

## Frontend cannot reach backend

For local/frontend development, verify:

- `VITE_API_URL`
- `VITE_SOCKET_URL`
- `VITE_BACKEND_PROXY_TARGET`

The default Vite proxy setup expects `/api` and `/socket.io` to target the backend service.

## Restart/update behavior is confusing

Remember:

- `/restart` performs a `git pull`
- restart exits with code `42`
- a process manager must bring the bot back up

For local work, `make run-direct` is often simpler than the restart wrapper.

## `make run` behaves oddly after restart

`start.sh` currently assumes a `venv/` path, but the Makefile creates `.venv/` by default.

If you see restart-wrapper issues:

- use `make run-direct`, or
- rename/create the expected virtualenv path, or
- update the wrapper for your machine

## Tests fail unexpectedly

Check that you installed both runtime and test dependencies:

```bash
make install
make install-test
```

Then retry with:

```bash
make test-verbose
```
