# Documentation guide

This folder is organized so you can go from **orientation** -> **configuration** -> **running** -> **operating** without guessing where things live.

## Recommended reading order

1. `architecture.md` - understand the moving pieces
2. `commands.md` - confirm the current user-facing surface
3. `configuration.md` - fill in env vars correctly
4. `development.md` - choose your local workflow
5. `mariadb_setup.md` - verify logging storage setup
6. `troubleshooting.md` - debug common issues quickly

## Doc map

| File | Best for |
| --- | --- |
| `architecture.md` | Visual/system-level understanding of how the bot, services, DB, and log viewer fit together |
| `commands.md` | Seeing exactly which slash commands currently exist and how they map to services/state |
| `configuration.md` | Filling in env vars and understanding defaults and production requirements |
| `development.md` | Running the bot locally, using Docker, and executing tests/checks |
| `deployment.md` | Hosting/runtime expectations and restart/update behavior |
| `mariadb_setup.md` | Creating/initializing the MariaDB schema used by logging and the log viewer |
| `troubleshooting.md` | Solving the most common runtime, auth, proxy, and downloader problems |
| `auto-update.md` | Understanding `KEEP_UP_TO_DATE_WITH_GIT`, restart flow, and announcements |

## If you're in a hurry

| Goal | Fastest path |
| --- | --- |
| Get the bot running locally | `configuration.md` -> `development.md` |
| Understand mention flow | `architecture.md` -> “mention pipeline” |
| Debug downloader behavior | `architecture.md` + `troubleshooting.md` |
| Bring up the whole stack | `development.md` + `mariadb_setup.md` |
| Work on the log viewer | `development.md` + `../log-viewer/frontend/README.md` |
