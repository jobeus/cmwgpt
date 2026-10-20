# Redis setup

Redis holds everything the bot keeps between restarts:

| Keys | What | Expires |
| --- | --- | --- |
| `cmwgpt:memory:<guild>:<user>` | One user's memory profile, right now (no history), max 100 KB | Never |
| `cmwgpt:memory_job:<guild>` | Nightly memory job progress (`processed_through`, last status) | Never |
| `cmwgpt:memory_migrated` | Set once the old MariaDB memory was copied in | Never |
| `cmwgpt:cache:<name>:<key>` | Fetched content: articles, YouTube/TikTok/Facebook transcripts, tweets and Instagram posts (with base64 images), image data URLs, voice-note transcripts | 30 days after last use (images 7 days, failed fetches 1 hour) |

If Redis is down the bot still replies: content caches fall back to memory and refetch, but `/memory` and the nightly job fail until it's back.

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

## First start after switching

On its first start the bot copies every non-empty `user_memory` profile and the `user_memory_job_state` rows out of MariaDB into Redis, then sets `cmwgpt:memory_migrated` so it never does it again. Look for this in the logs:

```
Moved 12 of 12 user memories and 1 job states from MariaDB to Redis
```

If MariaDB can't be reached it logs a warning and tries again on the next start. Profiles already in Redis are never overwritten by the copy. The old `.cache/*.json` content caches are imported the same way and renamed to `*.json.migrated`.

Check it worked:

```bash
redis-cli --scan --pattern 'cmwgpt:memory:*' | wc -l
redis-cli get cmwgpt:memory:<guild_id>:<user_id>
```

Once you're happy, MariaDB is no longer used and can be stopped. To re-run the copy, `redis-cli del cmwgpt:memory_migrated` and restart the bot.

## Useful commands

| Task | Command |
| --- | --- |
| Memory used | `redis-cli info memory \| grep used_memory_human` |
| Count cache entries | `redis-cli --scan --pattern 'cmwgpt:cache:*' \| wc -l` |
| Drop all content caches | `redis-cli --scan --pattern 'cmwgpt:cache:*' \| xargs -r redis-cli del` |
| Back up | `redis-cli bgsave` then copy `/var/lib/redis/dump.rdb` and `appendonlydir/` |
