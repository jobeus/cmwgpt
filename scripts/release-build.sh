#!/usr/bin/env bash
# Make this folder runnable: .venv/ with the newest release of everything in
# requirements.txt, and the code compiles. deployd runs it in each new release folder on
# grizzly (so a fresh .venv every deploy; a failure leaves the live release alone); `make
# run` runs it locally.
# eager: also upgrades what our requirements depend on, so nothing stays on an old release.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."

[ -x .venv/bin/python ] || "${PYTHON:-python3}" -m venv .venv
.venv/bin/python -m pip install --quiet --disable-pip-version-check \
    --upgrade --upgrade-strategy eager pip -r requirements.txt
.venv/bin/python -m compileall -q main.py src
