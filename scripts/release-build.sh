#!/usr/bin/env bash
# Bring .venv/ in line with requirements.txt and check the code compiles. Run by start.sh
# before every launch, so after a deploy (/restart or the webhook: git pull, then exit 42)
# the new requirements are installed before the bot comes back.
# eager: also upgrades what our requirements depend on, so nothing stays on an old release.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."

[ -x .venv/bin/python ] || "${PYTHON:-python3}" -m venv .venv
.venv/bin/python -m pip install --quiet --disable-pip-version-check \
    --upgrade --upgrade-strategy eager pip -r requirements.txt
.venv/bin/python -m compileall -q main.py src
