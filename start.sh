#!/bin/bash

# Discord Bot Auto-Restart Script
# This script runs the Discord bot and automatically restarts it when it exits with code 42

set -e  # Exit on any error

# Configuration
BOT_SCRIPT="main.py"
PYTHON_CMD="python"
RESTART_EXIT_CODE=42
MAX_RESTART_ATTEMPTS=10
RESTART_DELAY=2

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[$(date '+%H:%M:%S')]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[$(date '+%H:%M:%S')]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[$(date '+%H:%M:%S')]${NC} $1"
}

print_error() {
    echo -e "${RED}[$(date '+%H:%M:%S')]${NC} $1"
}

print_restart() {
    echo -e "${PURPLE}[$(date '+%H:%M:%S')]${NC} $1"
}

# Function to check if Python script exists
check_bot_script() {
    if [ ! -f "$BOT_SCRIPT" ]; then
        print_error "Bot script '$BOT_SCRIPT' not found!"
        exit 1
    fi
}

# Function to check if virtual environment is activated
check_venv() {
    if [ -z "$VIRTUAL_ENV" ]; then
        print_warning "No virtual environment detected. Consider using 'source venv/bin/activate' first."
    else
        print_status "Using virtual environment: $VIRTUAL_ENV"
    fi
}

# Function to get git commit info
get_git_info() {
    if git rev-parse --git-dir > /dev/null 2>&1; then
        local commit=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
        local branch=$(git branch --show-current 2>/dev/null || echo "unknown")
        echo "($branch:$commit)"
    else
        echo "(no git)"
    fi
}

# Function to run the bot
run_bot() {
    local attempt=$1
    local git_info=$(get_git_info)
    
    if [ $attempt -eq 1 ]; then
        print_status "Starting Discord bot $git_info"
    else
        print_restart "Restarting Discord bot $git_info (attempt $attempt)"
    fi
    
    # Run the bot and capture exit code
    $PYTHON_CMD "$BOT_SCRIPT"
    return $?
}

# Function to handle restart
handle_restart() {
    local exit_code=$1
    local attempt=$2
    
    if [ $exit_code -eq $RESTART_EXIT_CODE ]; then
        print_restart "Bot requested restart (exit code $exit_code)"
        
        if [ $attempt -ge $MAX_RESTART_ATTEMPTS ]; then
            print_error "Maximum restart attempts ($MAX_RESTART_ATTEMPTS) reached. Exiting."
            exit 1
        fi
        
        print_status "Waiting ${RESTART_DELAY}s before restart..."
        sleep $RESTART_DELAY
        return 0  # Continue restarting
    else
        if [ $exit_code -eq 0 ]; then
            print_success "Bot exited normally (exit code $exit_code)"
        else
            print_error "Bot exited with error (exit code $exit_code)"
        fi
        return 1  # Stop restarting
    fi
}

# Function to handle signals
cleanup() {
    print_warning "Received interrupt signal. Shutting down..."
    exit 0
}

# Main function
main() {
    # Set up signal handlers
    trap cleanup SIGINT SIGTERM
    
    print_status "Discord Bot Auto-Restart Script"
    print_status "================================"
    
    # Pre-flight checks
    check_bot_script
    check_venv
    
    local attempt=1
    
    while true; do
        # Run the bot
        run_bot $attempt
        local exit_code=$?
        
        # Handle the exit
        if handle_restart $exit_code $attempt; then
            attempt=$((attempt + 1))
            continue
        else
            break
        fi
    done
    
    print_status "Bot runner exiting."
}

# Help function
show_help() {
    echo "Discord Bot Auto-Restart Script"
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -h, --help     Show this help message"
    echo "  -s, --script   Specify bot script (default: main.py)"
    echo "  -p, --python   Specify Python command (default: python)"
    echo "  -m, --max      Maximum restart attempts (default: 10)"
    echo "  -d, --delay    Delay between restarts in seconds (default: 2)"
    echo ""
    echo "The script will automatically restart the bot when it exits with code 42."
    echo "Any other exit code will stop the restart loop."
    echo ""
    echo "Examples:"
    echo "  $0                           # Run with defaults"
    echo "  $0 -s bot.py -p python3      # Custom script and Python command"
    echo "  $0 -m 5 -d 5                 # Max 5 restarts with 5s delay"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            show_help
            exit 0
            ;;
        -s|--script)
            BOT_SCRIPT="$2"
            shift 2
            ;;
        -p|--python)
            PYTHON_CMD="$2"
            shift 2
            ;;
        -m|--max)
            MAX_RESTART_ATTEMPTS="$2"
            shift 2
            ;;
        -d|--delay)
            RESTART_DELAY="$2"
            shift 2
            ;;
        *)
            print_error "Unknown option: $1"
            echo "Use -h or --help for usage information."
            exit 1
            ;;
    esac
done

# Run the main function
main
