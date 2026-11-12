# Redis setup

Redis holds everything the bot keeps between restarts:

| Keys | What | Expires |
| --- | --- | --- |
| `cmwgpt:memory:<guild>:<user>` | One user's memory profile, right now (no history), max 100 KB | Never |
| `cmwgpt:memory_users:<guild>` | Set of the user IDs with a profile (plus a `built` marker); rebuilt from the profile keys if deleted | Never |
| `cmwgpt:memory_job:<guild>` | Nightly memory job progress (`processed_through`, last status) | Never |
| `cmwgpt:costs:<YYYY-MM-DD>` | That day's spending (America/Denver): one field per provider, per person and per channel, for `/cost` | 400 days |
| `cmwgpt:reminders:due`, `cmwgpt:reminder:<id>`, `cmwgpt:reminders:next_id` | Pending reminders (a sorted set by due time, one hash each) and the ID counter | Removed once posted |
| `cmwgpt:paper:players`, `cmwgpt:paper:positions:<user>`, `cmwgpt:paper:orders:open`, `cmwgpt:paper:orders:done:<user>`, `cmwgpt:paper:orders:next` | The paper-trading game's ledger: each player's cash, their holdings, orders still open at Alpaca, each player's last 20 finished orders, and the order-number counter | Never (cleared by a game reset) |
| `cmwgpt:timezones:<guild>` | Each person's saved time zone (IANA name) | Never |
| `cmwgpt:settings:channels`, `cmwgpt:settings:death` | Each channel's `/model`, `/imagemodel` and `/systemprompt` (one JSON field per channel), and the death-announcement settings | Never |
| `cmwgpt:death:names` | Names on Wikipedia's recent-deaths page already seen and announced | Never |
| `cmwgpt:package_update_trigger` | The package releases the last update restart was for, so an upgrade that didn't install doesn't restart the bot again | Never |
| `cmwgpt:restart_state` | Active channels, last git SHA and why the bot stopped, written on shutdown and taken by the next start | 1 day |
| `cmwgpt:context:history_window`, `cmwgpt:context:anthropic`, `cmwgpt:context:gemini` | Per channel: where the mention history window starts, Claude's cache breakpoint and previous response id, and Gemini's interaction id. A restart within the hour sends the same prompt, so the prompt cache still reads | 1 hour after the last mention (Gemini 10 minutes) |
| `cmwgpt:cache:<name>:<key>` | Fetched content: articles, YouTube/TikTok/Facebook transcripts, Gemini's YouTube summaries and `watch_video` answers, tweets, Instagram and Reddit posts (with base64 images), image data URLs, voice-note transcripts, Gemini video uploads | 30 days after last use (images 7 days, Gemini video uploads 47 hours, failed fetches 1 hour) |

If Redis is down the bot still replies: content caches fall back to memory and refetch, settings and conversation state are kept in memory only (changes made meanwhile are lost on restart), and `/memory` and the nightly job fail until it's back.

The JSON files these replaced (`.cache/cmwgpt_settings.json`, `.cache/cmwgpt_death_names.json`, `/tmp/cmwgpt_state_backup_*.json`) are imported on the first start with Redis and renamed to `*.migrated` (the `/tmp` ones are deleted).

## Install on the server (Debian/Ubuntu, no Docker)

```bash
sudo apt update && sudo apt install -y redis-server
```

Append the bot's settings to the end of the config (later lines win over the defaults above them):

```bash
sudo tee -a /etc/redis/redis.conf >/dev/null <<'EOF'

# --- cmwgpt ---
bind 127.0.0.1 -::1
protected-mode yes
supervised systemd
# Memories have no TTL, so volatile-lru only ever evicts cache entries.
maxmemory 512mb
maxmemory-policy volatile-lru
# Write memories to disk every second as well as the periodic snapshot.
appendonly yes
appendfsync everysec
EOF
sudo systemctl enable --now redis-server
sudo systemctl restart redis-server
redis-cli ping   # PONG
```

Redis only listens on localhost, so no password is needed. The bot's default `REDIS_URL` is `redis://127.0.0.1:6379/0`; set it in `.env` only if yours differs.

## Check it

```bash
redis-cli --scan --pattern 'cmwgpt:memory:*' | wc -l
redis-cli get cmwgpt:memory:<guild_id>:<user_id>
```

## Useful commands

| Task | Command |
| --- | --- |
| Memory used | `redis-cli info memory \| grep used_memory_human` |
| Count cache entries | `redis-cli --scan --pattern 'cmwgpt:cache:*' \| wc -l` |
| Drop all content caches | `redis-cli --scan --pattern 'cmwgpt:cache:*' \| xargs -r redis-cli del` |
| Back up | `redis-cli bgsave` then copy `/var/lib/redis/dump.rdb` and `appendonlydir/` |
