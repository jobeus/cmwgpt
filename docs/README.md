# Documentation guide

This folder is organized so you can go from **orientation** -> **configuration** -> **running** -> **operating** without guessing where things live.

## Recommended reading order

1. `architecture.md` - understand the moving pieces
2. `commands.md` - confirm the current user-facing surface
3. `configuration.md` - fill in env vars correctly
4. `development.md` - choose your local workflow
5. `redis_setup.md` - set up Redis (memory and caches)
6. `troubleshooting.md` - debug common issues quickly

## Doc map

| File | Best for |
| --- | --- |
| `architecture.md` | Visual/system-level understanding of how the bot, services, and memory DB fit together |
| `commands.md` | Seeing exactly which slash commands currently exist and how they map to services/state |
| `configuration.md` | Filling in env vars and understanding defaults and production requirements |
| `development.md` | Running the bot locally, using Docker, and executing tests/checks |
| `deployment.md` | Hosting/runtime expectations and restart/update behavior |
| `redis_setup.md` | Installing Redis, which holds per-user memory and the content caches |
| `mariadb_setup.md` | The old MariaDB memory tables, now only read once to import into Redis |
| `troubleshooting.md` | Solving the most common runtime, DB, proxy, and downloader problems |
| `auto-update.md` | Deploy webhook, restart flow, and announcements |

## If you're in a hurry

| Goal | Fastest path |
| --- | --- |
| Get the bot running locally | `configuration.md` -> `development.md` |
| Understand mention flow | `architecture.md` -> “mention pipeline” |
| Debug downloader behavior | `architecture.md` + `troubleshooting.md` |
| Bring up the whole stack | `development.md` + `redis_setup.md` |
