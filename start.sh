#!/usr/bin/env bash
# Runs cmwgpt. On grizzly this is what tmux-app@cmwgpt.service starts (inside tmux), from
# the live release in /srv/apps/cmwgpt; deployd builds each release
# (scripts/release-build.sh) before it gets here. Locally: `make run` builds, then runs this.
cd "$(dirname "$(readlink -f "$0")")" || exit 1

if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

exec .venv/bin/python main.py
