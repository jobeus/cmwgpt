#!/usr/bin/env bash
# Runs cmwgpt, relaunching it whenever it exits with code 42. The deploy webhook exits that
# way after it has pulled and built a new commit, so the build below is normally a no-op;
# it does real work after a manual pull or on first start. Any other exit code ends the
# loop (systemd's Restart=always covers crashes).
cd "$(dirname "$(readlink -f "$0")")" || exit 1

if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

while true; do
    if ! scripts/release-build.sh; then
        echo "start.sh: build failed, starting what is installed" >&2
    fi

    .venv/bin/python main.py
    code=$?
    echo "start.sh: cmwgpt exited with code $code"

    if [ "$code" -ne 42 ]; then
        exit "$code"
    fi
    echo "start.sh: restarting onto the new version"
    sleep 1
done
