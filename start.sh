#!/bin/bash

# Detect the project virtual environment (.venv preferred, venv legacy)
if [ -x ".venv/bin/python" ]; then
    VENV_BIN=".venv/bin"
elif [ -x "venv/bin/python" ]; then
    VENV_BIN="venv/bin"
else
    echo "No virtual environment found (.venv/ or venv/). Run 'make venv install' first." >&2
    exit 1
fi

PYTHON="$VENV_BIN/python"
PIP="$VENV_BIN/pip"

while true; do
    "$PYTHON" main.py
    exit_code=$?

    echo "Script exited with code $exit_code"

    # Check if bot requested restart
    if [ $exit_code -eq 42 ]; then
        echo "Restarting script because it exited with code 42"
        echo "Running pip install requirements..."
        "$PIP" install --upgrade -r requirements.txt
        sleep 1
    else
        echo "Not restarting. Exiting."
        break
    fi
done
