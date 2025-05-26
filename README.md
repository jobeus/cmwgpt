# 🤖 Advanced AI Discord Bot

[![CI](https://github.com/username/chatter/workflows/Continuous%20Integration/badge.svg)](https://github.com/username/chatter/actions)
[![Code Quality](https://github.com/username/chatter/workflows/Pull%20Request%20Checks/badge.svg)](https://github.com/username/chatter/actions)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code style: autopep8](https://img.shields.io/badge/code%20style-autopep8-000000.svg)](https://github.com/hhatto/autopep8)

A powerful, feature-rich Discord bot that integrates multiple AI models for chat, image generation, and intelligent conversation management. Built with Python and designed for seamless Discord server integration.

## ✨ Features

### 🗣️ **Intelligent Chat System**
- **Multi-Model Support**: Switch between GPT-4.1-mini, GPT-4.1-nano, and GPT-4o-mini
- **Contextual Conversations**: Maintains conversation history per channel
- **Image Understanding**: Upload images with your messages for AI analysis
- **Smart Mentions**: Bot responds intelligently when mentioned, analyzing recent channel context
- **Username Integration**: Optional username inclusion in conversations for personalized responses

### 🎨 **Advanced Image Generation**
- **Multiple AI Models**: Support for DALL-E 2, DALL-E 3, and GPT-Image-1
- **Image Editing**: Edit existing images with AI-powered modifications
- **High-Quality Output**: Generate stunning images from text prompts
- **Flexible Formats**: Automatic handling of different image formats and sizes

### ⚙️ **Customization & Management**
- **Channel-Specific System Prompts**: Set unique AI personalities per channel
- **Conversation Reset**: Clear chat history when needed
- **Model Switching**: Change AI models on-the-fly per channel
- **Configurable Behavior**: Extensive environment variable configuration

### 🔧 **Smart Technical Features**
- **Auto-Paste Integration**: Long responses automatically uploaded to paste.rs
- **Discord Limits Handling**: Intelligent message splitting for 2000+ character responses
- **Member Recognition**: Automatic user mention mapping and legend generation
- **Typing Indicators**: Visual feedback during AI processing
- **Error Handling**: Robust error management with user-friendly messages

### 📊 **Monitoring & Logging**
- **Comprehensive Logging**: Detailed logs for debugging and monitoring
- **Channel Tracking**: Per-channel conversation and model tracking
- **Usage Analytics**: Built-in logging for command usage and performance

## 🚀 Quick Start

### Prerequisites
- **Python 3.9 or later**
- **Discord Bot Token** (from Discord Developer Portal)
- **OpenAI API Key** (from OpenAI Platform)

### Installation

1. **Clone and Setup**
   ```bash
   git clone <repository-url>
   cd chatter
   pip3 install -r requirements.txt
   ```

2. **Environment Configuration**
   ```bash
   cp env.example .env
   # Edit .env with your tokens and preferences
   ```

3. **Run the Bot**
   ```bash
   python3 bot.py
   ```

## 🔧 Configuration

### Discord Bot Setup

1. **Create Application**: Visit [Discord Developer Portal](https://discord.com/developers/applications)
2. **Create Bot**: Build a Discord bot under your application
3. **Get Token**: Copy the bot token from bot settings

   ![Discord Bot Token](https://user-images.githubusercontent.com/89479282/205949161-4b508c6d-19a7-49b6-b8ed-7525ddbef430.png)

4. **Configure Intents**: Enable "MESSAGE CONTENT INTENT"

   ![Message Content Intent](https://user-images.githubusercontent.com/89479282/205949323-4354bd7d-9bb9-4f4b-a87e-deb9933a89b5.png)

5. **Invite Bot**: Use OAuth2 URL Generator with appropriate permissions

   ![OAuth2 Setup](https://user-images.githubusercontent.com/89479282/205949600-0c7ddb40-7e82-47a0-b59a-b089f929d177.png)

### OpenAI API Setup

1. **Get API Key**: Visit [OpenAI API Keys](https://platform.openai.com/api-keys)
2. **Add to Environment**: Set `OPENAI_API_KEY` in your `.env` file

### Environment Variables

```env
# Required
DISCORD_BOT_TOKEN=your_discord_bot_token
OPENAI_API_KEY=your_openai_api_key

# Optional Customization
SYSTEM_PROMPT="You are a helpful assistant"
DEFAULT_MODEL=gpt-4.1-nano
DEFAULT_IMAGE_MODEL=gpt-image-1
INCLUDE_USERNAMES=true
REPLY_TO_MENTIONS=true
INCLUDE_NUM_CHATLINES=100
```

## 📖 Commands Reference

### 💬 Chat Commands

#### `/chat [message] [attachment]`
Start or continue a conversation with the AI
- **message**: Your text message to the AI
- **attachment**: Optional image for AI analysis
- **Features**:
  - Maintains conversation context
  - Supports image analysis
  - Auto-uploads long responses to paste.rs

#### **@mention** (Natural Mentions)
Mention the bot in any message for contextual responses
- Analyzes recent channel history
- Provides relevant, context-aware replies
- Maintains conversational flow

### 🎨 Image Commands

#### `/draw [prompt] [edit_image] [model]`
Generate or edit images with AI
- **prompt**: Description of the image you want
- **edit_image**: Optional image to modify
- **model**: Choose from dall-e-2, dall-e-3, or gpt-image-1
- **Features**:
  - High-quality image generation
  - Image editing capabilities
  - Multiple model support

### ⚙️ Management Commands

#### `/reset`
Clear conversation history for the current channel
- Resets to default system prompt
- Clears all previous context
- Maintains model selection

#### `/model [model_name]`
View or change the AI model for the current channel
- **Available Models**:
  - `gpt-4.1-mini` - Balanced performance and cost
  - `gpt-4.1-nano` - Fast and efficient
  - `gpt-4o-mini` - Optimized variant
- **Usage**:
  - `/model` - View current model
  - `/model gpt-4.1-mini` - Switch to specific model

#### `/systemprompt` (Group Commands)
Manage channel-specific AI personalities

##### `/systemprompt set [prompt]`
Set a custom system prompt for the current channel
- **prompt**: The personality/behavior instructions for the AI
- **Example**: `/systemprompt set You are a helpful coding assistant specializing in Python`

##### `/systemprompt view`
Display the current system prompt for the channel

##### `/systemprompt reset`
Reset to the default system prompt

## 🏗️ Architecture

### Core Components

- **`bot.py`**: Main Discord bot logic and command handlers
- **`openai_handler.py`**: OpenAI API integration and response processing
- **`config.py`**: Environment configuration and settings management
- **`bot_state.py`**: In-memory conversation and state management
- **`utils/discord_helper.py`**: Discord-specific utility functions
- **`utils/pasters.py`**: Automatic paste service integration

### Key Features

- **Stateful Conversations**: Per-channel conversation memory
- **Intelligent Context**: Automatic user mention mapping
- **Robust Error Handling**: Graceful failure management
- **Performance Optimized**: Efficient API usage and response handling

## 🔍 Advanced Usage

### Custom System Prompts
Create specialized AI assistants for different channels:
```
/systemprompt set You are a Python expert who provides concise, executable code examples
/systemprompt set You are a creative writing assistant who helps with storytelling
/systemprompt set You are a technical documentation specialist
```

### Image Analysis Workflows
1. Upload an image with `/chat`
2. Ask specific questions about the image
3. Request modifications with `/draw` using the edit feature

### Multi-Model Strategy
- Use `gpt-4.1-nano` for quick responses
- Use `gpt-4.1-mini` for complex reasoning
- Use `gpt-4o-mini` for specialized tasks

## 🛠️ Development

### Dependencies
```
discord.py - Discord API integration
python-dotenv - Environment variable management
openai - OpenAI API client
requests - HTTP requests for paste service
```

### Testing
Comprehensive unit test suite with 48+ tests covering all major functionality:

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

**Test Coverage:**
- ✅ Configuration loading and validation
- ✅ Bot state management (conversations, models, prompts)
- ✅ OpenAI API integration (chat, image generation)
- ✅ Discord utilities (mention handling, member mapping)
- ✅ Paste service integration
- ✅ Message handling and formatting
- ✅ Error handling and edge cases

### Logging
Comprehensive logging system tracks:
- Command usage and performance
- API interactions and errors
- Channel-specific activities
- User interactions and mentions

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. **Run tests to ensure everything works:**
   ```bash
   make test
   # or
   python tests/run_tests.py
   ```
5. Add tests for new functionality
6. Submit a pull request

### Development Workflow
```bash
# Set up development environment
make dev-setup

# Run tests before committing
make test

# Check code quality
make lint                    # Check for linting issues
make autofix                 # Auto-fix linting issues
make format                  # Format code with black (optional)
```

### Code Quality Standards
This project maintains enterprise-level code quality:

- **PEP8 Compliance**: All code follows Python PEP8 style guidelines
- **Automatic Linting**: Use `make lint` to check for issues
- **Auto-Fix**: Use `make autofix` to automatically fix most linting issues
- **Pre-Commit Hooks**: Automatic code quality enforcement on every commit
- **100% Test Coverage**: Comprehensive test suite with 48+ tests
- **Type Hints**: Function signatures include type hints where appropriate

### Git Pre-Commit Hooks
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

## 🚀 CI/CD & Deployment

### GitHub Actions Workflows

This project includes comprehensive CI/CD pipelines:

#### **Continuous Integration** (`.github/workflows/ci.yml`)
- **Triggers**: Push to main/master, Pull Requests
- **Python Versions**: 3.9, 3.10, 3.11, 3.12
- **Checks**: Linting, Testing, Coverage
- **Features**:
  - Dependency caching for faster builds
  - Multi-version Python testing
  - Code coverage reporting
  - Automatic formatting validation

#### **Pull Request Checks** (`.github/workflows/pr-checks.yml`)
- **Advanced PR Validation**: Title/description checks
- **Security Scanning**: Bandit for code security, Safety for dependencies
- **Targeted Testing**: Only tests files changed in PR
- **Size Analysis**: Warns about large PRs
- **Commit Message Validation**: Ensures meaningful commit messages

#### **Release Automation** (`.github/workflows/release.yml`)
- **Automatic Releases**: Triggered by version tags
- **Changelog Generation**: Auto-generated from git commits
- **Docker Images**: Builds and publishes to GitHub Container Registry
- **Security Validation**: Pre-release security scanning

### Docker Deployment

```bash
# Build locally
docker build -t discord-bot .

# Run with environment variables
docker run -d \
  --name discord-bot \
  -e DISCORD_BOT_TOKEN=your_token \
  -e OPENAI_API_KEY=your_key \
  discord-bot

# Or use GitHub Container Registry
docker pull ghcr.io/username/chatter:latest
```

### Production Deployment

1. **Environment Setup**:
   ```bash
   # Clone repository
   git clone https://github.com/username/chatter.git
   cd chatter

   # Set up environment
   make dev-setup
   ```

2. **Configuration**:
   ```bash
   # Copy and configure environment
   cp env.example .env
   # Edit .env with your tokens
   ```

3. **Run with Process Manager**:
   ```bash
   # Using systemd, pm2, or supervisor
   python bot.py
   ```

## 📄 License

This project is open source and available under the MIT License.

---

**Ready to enhance your Discord server with AI?** Follow the setup guide above and start chatting with your new AI assistant! 🚀
