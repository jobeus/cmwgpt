# 🤖 AI Discord Bot

[![CI](https://github.com/jobeus/cmwgpt/actions/workflows/ci.yml/badge.svg)](https://github.com/jobeus/cmwgpt/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An intelligent Discord bot that brings OpenAI's powerful AI models directly to your Discord server. Chat with AI, generate images, and get contextual responses - all through simple Discord commands and mentions.

## ✨ Key Features

- **Two Interaction Modes**: Use `/chat` for private conversations or `@mention` for contextual channel responses
- **Multi-Model AI**: Switch between GPT-4.1-mini, GPT-4.1-nano, and GPT-4o-mini
- **Image Generation**: Create images with DALL-E 2, DALL-E 3, and GPT-Image-1
- **Image Analysis**: Upload images for AI analysis and understanding
- **Channel-Specific Personalities**: Set custom AI behavior per channel
- **Smart Context**: Bot understands channel history when mentioned
- **Auto-Update**: Automatically updates from git and restarts with state preservation
- **Smart Restart**: Built-in restart script with automatic recovery and clean console output

## 🎯 How It Works

The bot operates in two distinct modes:

### 💬 **Separate Conversations** (`/chat`)
- Start private conversations with the AI using `/chat [message]`
- Each channel maintains its own conversation history
- Perfect for focused discussions and extended conversations
- Upload images for analysis alongside your messages

### 🗣️ **Contextual Responses** (`@mention`)
- Mention the bot (`@YourBot`) in any channel message
- Bot analyzes recent channel history for context
- Provides relevant responses based on ongoing discussions
- Great for getting AI input on current conversations

## 🚀 Quick Start

### Prerequisites
- **Python 3.9 or later**
- **Discord Bot Token** (from Discord Developer Portal)
- **OpenAI API Key** (from OpenAI Platform)

### Installation

1. **Clone and Setup**
   ```bash
   git clone https://github.com/jobeus/cmwgpt.git
   cd cmwgpt
   pip3 install -r requirements.txt
   ```

2. **Environment Configuration**
   ```bash
   cp env.example .env
   # Edit .env with your Discord bot token and OpenAI API key

   # Optional: Customize the AI system prompt
   cp system_prompt.txt.example system_prompt.txt
   # Edit system_prompt.txt to customize the AI's personality and behavior
   ```

3. **Run the Bot**
   ```bash
   # With auto-restart support (recommended)
   make run

   # Or run directly
   python3 main.py
   ```

## 🔧 Setup Guide

### 1. Discord Bot Setup

1. Visit [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application and bot
3. Copy the bot token from the "Bot" section
4. Enable "SERVER MEMBERS INTENT" In Bot settings
5. Enable "MESSAGE CONTENT INTENT" in Bot settings
6. Use OAuth2 URL Generator to invite the bot to your server
   - Required permissions: Send Messages, Use Slash Commands, Read Message History

### 2. OpenAI API Setup

1. Get your API key from [OpenAI Platform](https://platform.openai.com/api-keys)
2. Add both tokens to your `.env` file:

```env
DISCORD_BOT_TOKEN=your_discord_bot_token_here
OPENAI_API_KEY=your_openai_api_key_here
```

## 📖 Basic Commands

### Essential Commands

- **`/chat [message]`** - Start a conversation with the AI
  - Add images for analysis by attaching them to your message
  - Each channel maintains separate conversation history

- **`@YourBot [message]`** - Mention the bot for contextual responses
  - Bot analyzes recent channel messages for context
  - Great for getting AI input on ongoing discussions

- **`/draw [prompt]`** - Generate images with AI
  - Choose from DALL-E 2, DALL-E 3, or GPT-Image-1 models

### Management Commands

- **`/reset`** - Clear conversation history for the current channel
- **`/model [name]`** - View or change AI model (gpt-5-mini, gpt-5-nano, gpt-4.1-mini, gpt-4.1-nano)
- **`/systemprompt set [prompt]`** - Set custom AI personality for the channel
- **`/systemprompt view`** - View current system prompt
- **`/systemprompt reset`** - Reset to default system prompt
- **`/restart`** - Restart the bot with latest updates (admin only)

> 💡 **Tip**: Each Discord channel has its own conversation history and settings!

## 📚 Documentation

For detailed information, see the [docs/](docs/) folder:

- **[Architecture](docs/architecture.md)** - Technical architecture and design patterns
- **[Development](docs/development.md)** - Development setup, testing, and contributing
- **[Deployment](docs/deployment.md)** - CI/CD, Docker, and production deployment
- **[Configuration](docs/configuration.md)** - Detailed configuration options
- **[Commands](docs/commands.md)** - Complete command reference
- **[Auto-Update](docs/auto-update.md)** - Automatic git-based updates and restarts
- **[Function Calling](docs/function-calling.md)** - OpenAI function calling for dynamic user context
- **[Troubleshooting](docs/troubleshooting.md)** - Common issues and solutions

## 🚀 Quick Examples

### Custom AI Personalities
```bash
# Create a coding assistant
/systemprompt set You are a Python expert who provides concise, executable code examples

# Create a creative writing helper
/systemprompt set You are a creative writing assistant who helps with storytelling
```

### Testing Your Setup
```bash
# Run tests to make sure everything works
make test
```

## 🤝 Contributing

Interested in contributing? Check out [docs/development.md](docs/development.md) for the complete development guide including:
- Development environment setup
- Testing procedures
- Code quality standards
- Git workflow

## 📄 License

This project is open source and available under the MIT License.

---

**Ready to enhance your Discord server with AI?** Follow the setup guide above and start chatting with your new AI assistant! 🚀
