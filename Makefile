# Makefile for Discord Bot project

.PHONY: help install test test-verbose test-coverage test-specific clean lint format run

# Default target
help:
	@echo "Available commands:"
	@echo "  install       - Install dependencies"
	@echo "  install-test  - Install test dependencies"
	@echo "  test          - Run all tests"
	@echo "  test-verbose  - Run tests with verbose output"
	@echo "  test-coverage - Run tests with coverage report"
	@echo "  test-specific - Run specific test (usage: make test-specific TEST=config)"
	@echo "  lint          - Run code linting"
	@echo "  format        - Format code"
	@echo "  clean         - Clean up generated files"
	@echo "  run           - Run the Discord bot"

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
	@flake8 bot.py config.py openai_handler.py bot_state.py utils/ tests/ --max-line-length=120 --ignore=E501,W503 || (echo "❌ Linting issues found. Run 'make autofix' to fix them automatically." && exit 1)
	@echo "✅ No linting issues found!"

# Auto-fix linting issues
autofix:
	@echo "🔧 Auto-fixing linting issues..."
	@autopep8 --in-place --aggressive --aggressive bot.py config.py openai_handler.py bot_state.py utils/*.py tests/*.py
	@echo "✅ Auto-fix complete! Run 'make lint' to verify."

# Format code (if black is installed)
format:
	@if command -v black >/dev/null 2>&1; then \
		black *.py utils/ tests/ --line-length=120; \
	else \
		echo "black not installed. Install with: pip install black"; \
	fi

# Clean up generated files
clean:
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -exec rm -rf {} +
	rm -rf htmlcov/
	rm -rf .coverage
	rm -rf .pytest_cache/
	rm -f debug.txt

# Run the Discord bot
run:
	python bot.py

# Development setup (install everything)
dev-setup: install install-test install-hooks
	@echo "Development environment set up!"
	@echo "Run 'make test' to verify everything works."
	@echo "Git pre-commit hooks are now active!"
