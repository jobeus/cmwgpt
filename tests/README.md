# Discord Bot Test Suite

Comprehensive unit tests for the Discord Bot project, covering all major functionality and edge cases.

## 📁 Test Structure

```
tests/
├── __init__.py                    # Test package initialization
├── README.md                      # This file
├── run_tests.py                   # Test runner script
├── test_config.py                 # Configuration module tests
├── test_openai_handler.py         # OpenAI API integration tests
├── test_utils_discord_helper.py   # Discord utility function tests
├── test_utils_pasters.py          # Paste service integration tests
└── test_bot_functions.py          # Bot helper function tests
```

## 🧪 Test Coverage

### `test_config.py`
- ✅ Environment variable loading
- ✅ Default value fallbacks
- ✅ Boolean parsing (`true`/`false`, `1`/`0`)
- ✅ Integer parsing and validation
- ✅ Configuration validation

### `test_openai_handler.py`
- ✅ Chat completion API calls
- ✅ Multiple model support (GPT-5, GPT-5-mini, GPT-5-nano)
- ✅ Image generation (DALL-E 2, DALL-E 3, GPT-Image-1)
- ✅ Image editing functionality
- ✅ Base64 encoding/decoding
- ✅ Error handling for API failures
- ✅ Complex message structure handling

### `test_utils_discord_helper.py`
- ✅ Mention legend generation
- ✅ Guild member fetching
- ✅ Special character handling in usernames
- ✅ Large guild support
- ✅ Duplicate username handling
- ✅ Output format consistency

### `test_utils_pasters.py`
- ✅ Successful paste uploads
- ✅ Error handling (400, 500 status codes)
- ✅ Unicode and special character support
- ✅ Large text handling
- ✅ Network error handling
- ✅ Response text processing

### `test_bot_functions.py`
- ✅ Mention context preparation
- ✅ Channel reply handling (short/long messages)
- ✅ Interaction followup handling
- ✅ Paste service integration
- ✅ Discord message length limits
- ✅ JSON content serialization
- ✅ Username formatting
- ✅ Attachment URL formatting

## 🚀 Running Tests

### Using the Custom Test Runner

```bash
# Run all tests
python tests/run_tests.py

# Run specific test module
python tests/run_tests.py config
python tests/run_tests.py openai_handler
```

### Using unittest (Built-in)

```bash
# Run all tests
python -m unittest discover tests

# Run specific test file
python -m unittest tests.test_config
python -m unittest tests.test_openai_handler

# Run specific test class
python -m unittest tests.test_config.TestConfig

# Run specific test method
python -m unittest tests.test_config.TestConfig.test_default_values
```

### Using pytest (Recommended)

First install test dependencies:
```bash
pip install -r test_requirements.txt
```

Then run tests:
```bash
# Run all tests with coverage
pytest

# Run specific test file
pytest tests/test_config.py

# Run tests with specific markers
pytest -m unit
pytest -m "not slow"

# Run tests with verbose output
pytest -v

# Run tests with coverage report
pytest --cov=. --cov-report=html
```

## 📊 Test Metrics

- **Total Test Files**: 6
- **Total Test Methods**: 50+
- **Code Coverage**: Targets 90%+ for core functionality
- **Test Categories**: Unit tests, Integration tests, Error handling

## 🔧 Test Configuration

### Environment Setup
Tests automatically handle environment variable mocking and cleanup to ensure isolation between test runs.

### Async Testing
Async functions are properly tested using `asyncio` event loops and `AsyncMock` objects.

### Mocking Strategy
- **External APIs**: OpenAI API calls are mocked
- **Discord Objects**: Discord.py objects are mocked for testing
- **Network Requests**: HTTP requests are mocked
- **File Operations**: File I/O is mocked where appropriate

## 🐛 Debugging Tests

### Verbose Output
```bash
python tests/run_tests.py  # Shows detailed output
pytest -v -s               # Shows print statements
```

### Coverage Reports
```bash
pytest --cov=. --cov-report=html
# Open htmlcov/index.html in browser
```

### Individual Test Debugging
```bash
python -m unittest tests.test_config.TestConfig.test_default_values -v
```

## 📝 Writing New Tests

### Test Naming Convention
- Test files: `test_<module_name>.py`
- Test classes: `Test<ClassName>`
- Test methods: `test_<functionality_description>`

### Example Test Structure
```python
import unittest
from unittest.mock import patch, MagicMock

class TestNewFeature(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures before each test method."""
        pass

    def tearDown(self):
        """Clean up after each test method."""
        pass

    def test_basic_functionality(self):
        """Test basic functionality with valid input."""
        # Arrange
        # Act
        # Assert
        pass

    def test_error_handling(self):
        """Test error handling with invalid input."""
        with self.assertRaises(ExpectedException):
            # Test code that should raise exception
            pass
```

### Async Test Structure
```python
import asyncio
import unittest
from unittest.mock import AsyncMock

class TestAsyncFeature(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_async_function(self):
        async def run_test():
            # Your async test code here
            pass

        self.loop.run_until_complete(run_test())
```

## 🎯 Best Practices

1. **Test Isolation**: Each test should be independent
2. **Mock External Dependencies**: Don't make real API calls
3. **Test Edge Cases**: Include boundary conditions and error cases
4. **Clear Test Names**: Test names should describe what they test
5. **Arrange-Act-Assert**: Structure tests clearly
6. **Clean Up**: Always clean up resources in tearDown
7. **Use Subtests**: For testing multiple similar scenarios

## 🔍 Continuous Integration

These tests are designed to run in CI/CD environments. They:
- Don't require external network access (mocked)
- Don't require real Discord tokens
- Don't require real OpenAI API keys
- Clean up after themselves
- Provide clear pass/fail indicators

## 📈 Future Test Enhancements

- [ ] Integration tests with real Discord bot (optional)
- [ ] Performance benchmarking tests
- [ ] Load testing for conversation storage
- [ ] End-to-end workflow tests
- [ ] Security testing for input validation
