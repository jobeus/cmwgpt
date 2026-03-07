# Auto-update and restart behavior

## Overview

The bot has two related mechanisms:

- `AutoUpdateService` polls git for remote updates when enabled.
- `RestartHandler` performs stateful restarts, including a `git pull` before exit.

Relevant code:

- `src/services/auto_update_service.py`
- `src/services/restart_handler.py`
- `src/services/announcement_service.py`

## Environment variables

- `KEEP_UP_TO_DATE_WITH_GIT`
  - `true`: periodically poll for updates and restart when needed
  - `false`: do not poll automatically
- `QUIET_UPDATES`
  - controls whether restart/update announcements are suppressed

## What happens during a restart

When a restart is triggered, the bot:

1. saves state to a temporary file
2. performs `git pull`
3. exits with code `42`

The surrounding process manager or wrapper is expected to start it again.

## Manual restart command

The bot exposes `/restart`.

That command uses the same restart handler path, so it also performs a `git pull` before exiting.

## Startup announcements

On next boot, `AnnouncementService` checks saved restart metadata and can post a startup/update notice unless `QUIET_UPDATES` suppresses it.

## Recommended usage

- For production-like unattended hosting, use a process manager that restarts the bot when it exits.
- For local development, `KEEP_UP_TO_DATE_WITH_GIT=false` is usually simpler.
- Prefer `make run-direct` or `python main.py` during local development unless you specifically want restart-wrapper behavior.

## Important caveat for local runs

The repository's `start.sh` wrapper currently assumes a `venv/` virtualenv path when reinstalling dependencies after a restart. The Makefile's default environment path is `.venv/`.

If you use the wrapper locally, either:

- create/use `venv/`, or
- adjust the script for your environment, or
- use `make run-direct` instead.
