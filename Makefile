# Makefile for Discord Bot project

.PHONY: help install install-test install-hooks test test-verbose test-coverage test-specific lint autofix format security ci-test docker-build docker-run clean run dev-setup

# Default target
help:
	@echo "Available commands:"
	@echo "  install       - Install dependencies"
	@echo "  install-test  - Install test dependencies"
	@echo "  install-hooks - Install git pre-commit hooks"
	@echo "  test          - Run all tests"
	@echo "  test-verbose  - Run tests with verbose output"
	@echo "  test-coverage - Run tests with coverage report"
	@echo "  test-specific - Run specific test (usage: make test-specific TEST=config)"
	@echo "  lint          - Run code linting"
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

# Install dependencies
install:
	pip install -r requirements.txt

# Install test dependencies
install-test:
	pip install -r test_requirements.txt

# Install git pre-commit hooks
install-hooks:
	@echo "Installing git pre-commit hooks..."
	@./install-hooks.sh

# Run all tests
test:
	python tests/run_tests.py

# Run tests with verbose output
test-verbose:
	python -m unittest discover tests -v

# Run tests with coverage (requires pytest)
test-coverage:
	pytest --cov=. --cov-report=term-missing --cov-report=html

# Run specific test module
test-specific:
	python tests/run_tests.py $(TEST)

# Run code linting
lint:
	@echo "Running flake8 linting..."
	@if command -v flake8 >/dev/null 2>&1; then \
		flake8 main.py src/ tests/ --max-line-length=120 --ignore=E501,W503,W504,E999 || (echo "❌ Linting issues found. Run 'make autofix' to fix them automatically." && exit 1); \
		echo "✅ No linting issues found!"; \
	else \
		echo "❌ flake8 not installed. Install with: pip install -r test_requirements.txt"; \
		exit 1; \
	fi

# Auto-fix linting issues
autofix:
	@echo "🔧 Auto-fixing linting issues..."
	@if command -v autopep8 >/dev/null 2>&1; then \
		find src tests -name "*.py" -exec autopep8 --in-place --aggressive --aggressive {} \;; \
		autopep8 --in-place --aggressive --aggressive main.py; \
		echo "✅ Auto-fix complete! Run 'make lint' to verify."; \
	else \
		echo "❌ autopep8 not installed. Install with: pip install -r test_requirements.txt"; \
		exit 1; \
	fi

# Format code (if black is installed)
format:
	@if command -v black >/dev/null 2>&1; then \
		black main.py src/ tests/ --line-length=120; \
		echo "✅ Code formatting complete!"; \
	else \
		echo "❌ black not installed. Install with: pip install -r test_requirements.txt"; \
		exit 1; \
	fi

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
	@pip install bandit safety
	@echo "🔍 Running bandit security scan..."
	@bandit -r . -f json -o bandit-report.json || true
	@echo "🔍 Checking for dependency vulnerabilities..."
	@safety check || true
	@echo "✅ Security scan complete. Check bandit-report.json for details."

# Run CI-style tests locally
ci-test:
	@echo "Running CI-style tests locally..."
	@make lint
	@make test
	@make security
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
	./start.sh

# Run the Discord bot directly (without auto-restart)
run-direct:
	python main.py

# Development setup (install everything)
dev-setup: install install-test install-hooks
	@echo "Development environment set up!"
	@echo "Run 'make test' to verify everything works."
	@echo "Git pre-commit hooks are now active!"
