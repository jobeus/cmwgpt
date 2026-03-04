#!/bin/bash

while true; do
    python main.py
    exit_code=$?

    echo "Script exited with code $exit_code"

    # Check if bot requested restart
    if [ $exit_code -eq 42 ]; then
        echo "Restarting script because it exited with code 42"
        echo "Running pip install requirements..."
        venv/bin/pip install --upgrade -r requirements.txt
        sleep 1
    else
        echo "Not restarting. Exiting."
        break
    fi
done
