#!/bin/bash
#
# Git hooks installation script for Discord Bot project
# Sets up pre-commit hooks for automatic code quality checks
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_status() {
    echo -e "${BLUE}[INSTALL-HOOKS]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[INSTALL-HOOKS]${NC} ✅ $1"
}

print_warning() {
    echo -e "${YELLOW}[INSTALL-HOOKS]${NC} ⚠️  $1"
}

print_error() {
    echo -e "${RED}[INSTALL-HOOKS]${NC} ❌ $1"
}

# Check if we're in a git repository
check_git_repo() {
    if ! git rev-parse --git-dir > /dev/null 2>&1; then
        print_error "Not in a git repository!"
        exit 1
    fi
}

# Install dependencies required for hooks
install_dependencies() {
    print_status "Checking and installing hook dependencies..."
    
    local deps=("flake8" "autopep8")
    local missing_deps=()
    
    for dep in "${deps[@]}"; do
        if ! command -v "$dep" &> /dev/null; then
            missing_deps+=("$dep")
        fi
    done
    
    if [ ${#missing_deps[@]} -ne 0 ]; then
        print_status "Installing missing dependencies: ${missing_deps[*]}"
        pip install "${missing_deps[@]}"
        print_success "Dependencies installed"
    else
        print_success "All dependencies already installed"
    fi
}

# Configure git to use the hooks
configure_git() {
    print_status "Configuring git settings..."

    # Point git at the project's hooks directory; all hooks in .githooks
    # (including pre-commit) are picked up from there directly.
    git config core.hooksPath .githooks
    chmod +x .githooks/* 2>/dev/null || true

    print_success "Git configured to use project hooks (.githooks)"
}

# Main installation function
main() {
    print_status "Installing Git hooks for Discord Bot project..."
    
    # Check if we're in a git repo
    check_git_repo
    
    # Install dependencies
    install_dependencies

    # Configure git
    configure_git
    
    print_success "Git hooks installation complete! 🎉"
    echo
    print_status "The pre-commit hook will now:"
    echo "  • Check linting issues in staged Python files"
    echo "  • Auto-fix issues where possible"
    echo "  • Re-stage fixed files automatically"
    echo "  • Run tests to ensure functionality"
    echo "  • Prevent commits if issues can't be auto-fixed"
    echo
    print_status "To disable the hook temporarily, use: git commit --no-verify"
}

# Run main function
main "$@"
