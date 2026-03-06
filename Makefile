# Makefile for Discord Bot project

.PHONY: help venv install install-test install-hooks test test-verbose test-coverage test-specific lint typecheck autofix format security ci-test docker-build docker-run clean run run-direct dev-setup

VENV ?= .venv
PYTHON := $(VENV)/bin/python
PIP := $(PYTHON) -m pip

# Default target
help:
	@echo "Available commands:"
	@echo "  venv          - Create the local Python virtual environment"
	@echo "  install       - Install dependencies"
	@echo "  install-test  - Install test dependencies"
	@echo "  install-hooks - Install git pre-commit hooks"
	@echo "  test          - Run all tests"
	@echo "  test-verbose  - Run tests with verbose output"
	@echo "  test-coverage - Run tests with coverage report"
	@echo "  test-specific - Run specific test (usage: make test-specific TEST=config)"
	@echo "  lint          - Run code linting"
	@echo "  typecheck     - Run Python type checking"
	@echo "  autofix       - Auto-fix linting issues"
	@echo "  format        - Format code"
	@echo "  security      - Run security scans"
	@echo "  ci-test       - Run CI-style tests locally"
	@echo "  docker-build  - Build Docker image"
	@echo "  docker-run    - Run Docker container"
	@echo "  clean         - Clean up generated files"
	@echo "  run           - Run the Discord bot with auto-restart support"
	@echo "  run-direct    - Run the Discord bot directly (no auto-restart)"
	@echo "  dev-setup     - Complete development environment setup"

# Create local virtual environment
venv:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip

# Install dependencies
install:
	$(PIP) install -r requirements.txt

# Install test dependencies
install-test:
	$(PIP) install -r test_requirements.txt

# Install git pre-commit hooks
install-hooks:
	@echo "Installing git pre-commit hooks..."
	@./install-hooks.sh

# Run all tests
test:
	$(PYTHON) tests/run_tests.py

# Run tests with verbose output
test-verbose:
	$(PYTHON) -m unittest discover tests -v

# Run tests with coverage (requires pytest)
test-coverage:
	$(PYTHON) -m pytest --cov=. --cov-report=term-missing --cov-report=html

# Run specific test module
test-specific:
	$(PYTHON) tests/run_tests.py $(TEST)

# Run code linting
lint:
	@echo "Running flake8 linting from $(VENV)..."
	@$(PYTHON) -m flake8 main.py src/ tests/ --select=F63,F7,F82,E9 || (echo "❌ High-signal Python lint issues found." && exit 1)
	@echo "✅ No linting issues found!"

# Run Python type checks
typecheck:
	@echo "Running mypy type checks from $(VENV)..."
	@$(PYTHON) -m mypy
	@echo "✅ No type issues found!"

# Auto-fix linting issues
autofix:
	@echo "🔧 Auto-fixing linting issues..."
	@find src tests -name "*.py" -exec $(PYTHON) -m autopep8 --in-place --aggressive --aggressive {} \;
	@$(PYTHON) -m autopep8 --in-place --aggressive --aggressive main.py
	@echo "✅ Auto-fix complete! Run 'make lint' to verify."

# Format code (if black is installed)
format:
	@$(PYTHON) -m black main.py src/ tests/ --line-length=120
	@echo "✅ Code formatting complete!"

# Clean up generated files
clean:
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -exec rm -rf {} +
	rm -rf htmlcov/
	rm -rf .coverage
	rm -rf .pytest_cache/
	rm -f debug.txt

# Run security scans
security:
	@echo "Running security scans..."
	@$(PIP) install bandit safety
	@echo "🔍 Running bandit security scan..."
	@$(PYTHON) -m bandit -r src tests main.py -f json -o bandit-report.json || true
	@echo "🔍 Checking for dependency vulnerabilities..."
	@$(PYTHON) -m safety check || true
	@echo "✅ Security scan complete. Check bandit-report.json for details."

# Run CI-style tests locally
ci-test:
	@echo "Running CI-style tests locally..."
	@make lint
	@make typecheck
	@make test
	@echo "✅ All CI checks passed!"

# Build Docker image
docker-build:
	@echo "Building Docker image..."
	@docker build -t discord-bot:latest .
	@echo "✅ Docker image built successfully!"

# Run Docker container
docker-run:
	@echo "Running Docker container..."
	@docker run -d \
		--name discord-bot \
		--env-file .env \
		discord-bot:latest
	@echo "✅ Docker container started!"

# Run the Discord bot with auto-restart support
run:
	@echo "Starting Discord bot with auto-restart support..."
	@echo "Use Ctrl+C to stop the bot gracefully."
	@echo "Note: If you see 'Error 42', the bot is restarting automatically."
	@exec ./start.sh

# Run the Discord bot directly (without auto-restart)
run-direct:
	$(PYTHON) main.py

# Development setup (install everything)
dev-setup: venv install install-test install-hooks
	@echo "Development environment set up!"
	@echo "Run 'make test' to verify everything works."
	@echo "Git pre-commit hooks are now active!"
