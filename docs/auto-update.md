# Auto-update and restart behavior

How new code and new packages reach the running bot, and what `/restart` does.

## The moving parts

| Part | What it does |
| --- | --- |
| deployd (on grizzly, not in this repo) | The server's deploy agent (jobeus/jobepanel, `servers/grizzly/deployd`). Builds each release in its own folder under `/srv/apps/cmwgpt/releases/`, switches to it, restarts the bot, and switches back if the bot doesn't stay up. |
| `scripts/release-build.sh` | What deployd runs in a new release folder: a fresh `.venv/` with the newest release of everything in `requirements.txt`, and a compile check. |
| `src/services/restart_handler.py` | `/restart` and package updates: saves state, then asks deployd for a fresh release (`deploy request cmwgpt --force`). |
| `src/services/package_update_service.py` | Every `PACKAGE_UPDATE_CHECK_HOURS` (default 6) asks pip whether a new build would install anything newer, and asks for a restart when it would. |
| `src/services/announcement_service.py` | After a start on a different commit, posts what changed to the active channels (unless `QUIET_UPDATES`). |

The bot listens on no port. It used to run its own deploy webhook; deployd took that over.

## A push to `main`

1. CI runs. When it passes, its deploy job POSTs a signed request to
   `https://deploy.jobe.wtf/cmwgpt` (Actions secrets `DEPLOY_WEBHOOK_URL`, `DEPLOY_WEBHOOK_SECRET`).
2. deployd checks the commit out into a new release folder and builds it. The bot keeps running.
3. deployd restarts the unit: the bot gets SIGTERM and runs its normal shutdown (state saved to
   Redis, queue drained for up to 90 seconds, clients closed), then starts from the new folder.
4. If the new release doesn't stay up for 10 seconds, deployd switches back to the previous one.
   A failed build never touches the running bot.
5. On start the bot compares the saved commit with the one it is running and announces the changes.

## `/restart` and package updates

Both go through `RestartHandler.perform_restart`:

1. Save state to Redis (active channels, last commit, whether it was a manual `/restart`).
2. Ask deployd for a fresh release of the latest code, rebuilt even if that commit is already
   live. That is what picks up new package releases.
3. deployd builds it and restarts the bot as above. The shutdown saves state again and keeps
   the "manual restart" mark, so the announcement still knows.
4. If the release never replaces the process (its build failed), restarts are allowed again
   after 20 minutes; see `journalctl -u deployd` on grizzly for why.

Where there is no deploy agent (a local run: `APP_NAME` isn't set), step 2 is skipped: the
bot shuts down and exits with code `42`, and `make run` has to be run again.
`REDEPLOY_COMMAND` replaces the command the bot runs to ask.

## By hand, on grizzly

```
deploy status              # what is live
deploy cmwgpt              # deploy main now (or: deploy cmwgpt <commit>)
deploy cmwgpt --force      # rebuild the live commit with the newest packages
deploy rollback cmwgpt     # back to the release before
tmux -L cmwgpt a           # the bot's console (as jobe; detach with C-b d)
journalctl -t cmwgpt -f    # the bot's output;  journalctl -u deployd -f  for deploys
```

## Update announcements after restart

`QUIET_UPDATES=true` turns the announcements off; it does not change restarts. With no
active channel nothing is announced. A restart onto the same commit (a package update)
announces nothing.

## See also

- `docs/deployment.md`
- `docs/troubleshooting.md`
