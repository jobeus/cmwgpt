# 🛠️ Development Guide

## Development Setup

### Prerequisites
- **Python 3.9 or later**
- **Git**
- **Discord Bot Token**
- **OpenRouter API Key**

### Initial Setup

1. **Clone and Setup Environment**
   ```bash
   git clone https://github.com/jobeus/cmwgpt.git
   cd cmwgpt
   
   # Create virtual environment (recommended)
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   
   # Install dependencies
   pip install -r requirements.txt
   pip install -r test_requirements.txt
   ```

2. **Environment Configuration**
   ```bash
   cp env.example .env
   # Edit .env with your test bot token and API keys
   ```

3. **Development Environment Setup**
   ```bash
   # Set up development tools and pre-commit hooks
   make dev-setup
   
   # Or manually:
   make install-hooks
   ```

## Testing

### Running Tests

The project includes a comprehensive test suite with 48+ tests covering all major functionality.

```bash
# Run all tests
make test
# or
python tests/run_tests.py

# Run specific test module
make test-specific TEST=config
# or
python tests/run_tests.py config

# Run with coverage report
make test-coverage
# or
pytest --cov=. --cov-report=html

# Install test dependencies
make install-test
# or
pip install -r test_requirements.txt
```

### Test Coverage

**Current Test Coverage:**
- ✅ Configuration loading and validation
- ✅ Bot state management (conversations, models, prompts)
- ✅ OpenAI API integration (chat)
- ✅ Runpod.io integration (images)
- ✅ Discord utilities (mention handling, member mapping)
- ✅ Paste service integration
- ✅ Message handling and formatting
- ✅ Error handling and edge cases
- ✅ Queue service functionality
- ✅ Command processing and validation

### Writing Tests

When adding new functionality:

1. **Create test files** in the `tests/` directory
2. **Follow naming convention**: `test_[module_name].py`
3. **Use pytest fixtures** for common setup
4. **Mock external dependencies** (Discord API, OpenAI API)
5. **Test both success and error cases**

Example test structure:
```python
import pytest
from unittest.mock import Mock, patch
from src.services.your_service import YourService

class TestYourService:
    def test_success_case(self):
        # Test successful operation
        pass
    
    def test_error_handling(self):
        # Test error conditions
        pass
```

## Code Quality Standards

This project maintains enterprise-level code quality standards.

### Linting and Formatting

```bash
# Check for linting issues
make lint

# Auto-fix linting issues
make autofix

# Format code with black (optional)
make format
```

### Code Quality Features

- **PEP8 Compliance**: All code follows Python PEP8 style guidelines
- **Automatic Linting**: Automated checks for code quality issues
- **Auto-Fix**: Automatic fixing of common linting issues
- **Pre-Commit Hooks**: Quality enforcement on every commit
- **Type Hints**: Function signatures include type hints where appropriate

### Pre-Commit Hooks

Automatic code quality enforcement that runs on every commit:

```bash
# Install pre-commit hooks (one-time setup)
make install-hooks

# The hooks will automatically:
# ✅ Check linting issues in staged Python files
# ✅ Auto-fix issues where possible (spacing, imports, etc.)
# ✅ Re-stage fixed files automatically
# ✅ Run tests to ensure functionality
# ❌ Prevent commits if issues can't be auto-fixed

# To bypass hooks temporarily (not recommended)
git commit --no-verify -m "Emergency commit"
```

**What the hooks fix automatically:**
- Import statement formatting (`import os,sys` → `import os` + `import sys`)
- Function spacing (`def func( ):` → `def func():`)
- Operator spacing (`x=1+2` → `x = 1 + 2`)
- Trailing whitespace and blank lines
- Comment spacing and formatting

## Development Workflow

### Standard Workflow

```bash
# Set up development environment
make dev-setup

# Create feature branch
git checkout -b feature/your-feature-name

# Make your changes
# ... edit code ...

# Run tests before committing
make test

# Check code quality
make lint

# Commit changes (pre-commit hooks will run automatically)
git add .
git commit -m "Add your feature description"

# Push and create pull request
git push origin feature/your-feature-name
```

### Adding New Features

1. **Plan the feature** - Consider architecture and design patterns
2. **Write tests first** - Test-driven development approach
3. **Implement the feature** - Follow existing code patterns
4. **Update documentation** - Keep docs in sync with code
5. **Test thoroughly** - Run full test suite
6. **Submit pull request** - Include description and testing notes

## Project Dependencies

### Core Dependencies
```
discord.py - Discord API integration
python-dotenv - Environment variable management
openai - OpenAI API client
requests - HTTP requests for paste service
```

### Development Dependencies
```
pytest - Testing framework
pytest-cov - Coverage reporting
pytest-asyncio - Async testing support
autopep8 - Code formatting
flake8 - Linting
```

## Debugging

### Logging

The bot includes comprehensive logging:

```python
import logging
logger = logging.getLogger(__name__)

# Log levels used throughout the project
logger.info("Informational messages")
logger.warning("Warning conditions")
logger.error("Error conditions")
logger.debug("Debug information")
```

### Common Debug Scenarios

1. **Discord API Issues**
   - Check bot permissions in Discord server
   - Verify MESSAGE CONTENT INTENT is enabled
   - Check rate limiting in logs

2. **OpenAI API Issues**
   - Verify API key is valid and has credits
   - Check model availability
   - Review rate limiting and quotas

3. **Command Not Working**
   - Check slash command registration
   - Verify bot has necessary permissions
   - Review error logs for exceptions

### Development Bot Setup

For development, create a separate Discord bot:

1. Create new application in Discord Developer Portal
2. Create bot and get token
3. Enable MESSAGE CONTENT INTENT
4. Invite to test server with appropriate permissions
5. Use test bot token in development environment

## Contributing Guidelines

### Pull Request Process

1. **Fork the repository**
2. **Create a feature branch** from main
3. **Make your changes** following code standards
4. **Add tests** for new functionality
5. **Update documentation** as needed
6. **Run full test suite** to ensure nothing breaks
7. **Submit pull request** with clear description

### Pull Request Requirements

- ✅ All tests pass
- ✅ Code follows style guidelines
- ✅ New features include tests
- ✅ Documentation is updated
- ✅ Commit messages are descriptive
- ✅ No merge conflicts with main branch

### Code Review Process

- All pull requests require review
- Automated checks must pass
- Manual testing may be required
- Feedback should be addressed promptly

## Release Process

### Version Management

- Follow semantic versioning (MAJOR.MINOR.PATCH)
- Update version in relevant files
- Create git tags for releases

### Release Checklist

1. ✅ All tests pass
2. ✅ Documentation is up to date
3. ✅ Version numbers are updated
4. ✅ Changelog is updated
5. ✅ Security scan passes
6. ✅ Create release tag
7. ✅ Deploy to production

## Getting Help

### Resources

- **Architecture Documentation**: [architecture.md](architecture.md)
- **Configuration Guide**: [configuration.md](configuration.md)
- **Troubleshooting**: [troubleshooting.md](troubleshooting.md)

### Community

- **GitHub Issues**: Report bugs and request features
- **GitHub Discussions**: Ask questions and share ideas
- **Pull Requests**: Contribute code improvements

### Best Practices

- **Read existing code** to understand patterns
- **Start with small changes** to get familiar
- **Ask questions** if anything is unclear
- **Test thoroughly** before submitting
- **Document your changes** clearly
