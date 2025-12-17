# ⚙️ Configuration Guide

## Environment Variables

The bot is configured through environment variables defined in a `.env` file. Copy `env.example` to `.env` and customize the values.

### Required Configuration

#### `DISCORD_BOT_TOKEN`
- **Required**: Yes
- **Description**: Your Discord bot token from the Discord Developer Portal
- **Example**: `DISCORD_BOT_TOKEN=1234567890abcdef1234567890abcdef12345678`
- **How to get**:
  1. Visit [Discord Developer Portal](https://discord.com/developers/applications)
  2. Create or select your application
  3. Go to "Bot" section
  4. Copy the token

#### `OPENAI_API_KEY`
- **Required**: Yes
- **Description**: Your OpenAI API key for AI model access
- **Example**: `OPENAI_API_KEY=sk-1234567890abcdef1234567890abcdef12345678`
- **How to get**:
  1. Visit [OpenAI Platform](https://platform.openai.com/api-keys)
  2. Create a new API key
  3. Copy the key (save it immediately as it won't be shown again)

### Optional Configuration

#### `DISCORD_CHANNEL_ID`
- **Required**: No
- **Description**: Specific channel ID to restrict bot operation (if not set, works in all channels)
- **Example**: `DISCORD_CHANNEL_ID=1234567890123456789`
- **Default**: None (works in all channels)
- **How to get**: Enable Developer Mode in Discord, right-click channel, "Copy ID"

#### System Prompt Configuration
- **File**: `system_prompt.txt` (optional)
- **Description**: Default system prompt for AI personality and behavior
- **Example**: See `system_prompt.txt.example` for template
- **Default**: Falls back to "You are a helpful assistant." if file doesn't exist
- **Features**:
  - Multi-line support for complex prompts
  - Dynamic date/time insertion with `[[CURRENT_DATE_AND_TIME]]` variable
  - Better readability than environment variables
- **Notes**: Can be overridden per channel using `/systemprompt set`

#### `DEFAULT_MODEL`
- **Required**: No
- **Description**: Default OpenAI model to use for chat
- **Example**: `DEFAULT_MODEL=gpt-5-mini`
- **Default**: `gpt-5-mini`
- **Available Options**:
  - `gpt-5` - Latest GPT-5 model with best performance
  - `gpt-5-mini` - Balanced performance and cost (recommended)
  - `gpt-5-nano` - Fast and efficient for quick responses

#### `DEFAULT_IMAGE_MODEL`
- **Required**: No
- **Description**: Default model for image generation
- **Example**: `DEFAULT_IMAGE_MODEL=dall-e-3`
- **Default**: `gpt-image-1.5`
- **Available Options**:
  - `dall-e-2` - DALL-E 2 model
  - `dall-e-3` - DALL-E 3 model (higher quality)
  - `gpt-image-1` - GPT Image model

#### `INCLUDE_USERNAMES`
- **Required**: No
- **Description**: Whether to include usernames in conversation context
- **Example**: `INCLUDE_USERNAMES=true`
- **Default**: `true`
- **Options**: `true`, `false`
- **Effect**: When enabled, messages include `<@user_id> says: message`

#### `REPLY_TO_MENTIONS`
- **Required**: No
- **Description**: Whether the bot should respond to @mentions
- **Example**: `REPLY_TO_MENTIONS=true`
- **Default**: `true`
- **Options**: `true`, `false`
- **Effect**: Enables/disables contextual responses when bot is mentioned

#### `INCLUDE_NUM_CHATLINES`
- **Required**: No
- **Description**: Number of recent messages to include for context in mentions
- **Example**: `INCLUDE_NUM_CHATLINES=50`
- **Default**: `100`
- **Range**: 1-200 (recommended: 50-100)
- **Effect**: More lines = better context but higher token usage

#### `KEEP_UP_TO_DATE_WITH_GIT`
- **Required**: No
- **Description**: Enable automatic git-based updates and restarts
- **Example**: `KEEP_UP_TO_DATE_WITH_GIT=true`
- **Default**: `false`
- **Options**: `true`, `false`
- **Effect**: When enabled, bot monitors git repository for updates and automatically restarts
- **See**: [Auto-Update Documentation](auto-update.md) for detailed configuration

#### `QUIET_UPDATES`
- **Required**: No
- **Description**: Control whether bot announces updates after restart
- **Example**: `QUIET_UPDATES=true`
- **Default**: `false`
- **Options**: `true`, `false`
- **Effect**: When enabled, bot skips update announcements to channels

#### `USER_CONTEXT_URL`
- **Required**: No
- **Description**: URL endpoint for OpenAI function calling to fetch user context
- **Example**: `USER_CONTEXT_URL=https://your-server.com/api/user-context`
- **Default**: None (function calling disabled)
- **Effect**: Enables dynamic user context fetching for personalized responses
- **See**: [Function Calling Documentation](function-calling.md) for setup details

## Complete Example Configuration

```env
# Required - Get these from Discord Developer Portal and OpenAI Platform
DISCORD_BOT_TOKEN=1234567890abcdef1234567890abcdef12345678
OPENAI_API_KEY=sk-1234567890abcdef1234567890abcdef12345678

# Optional - Channel restriction (remove to work in all channels)
DISCORD_CHANNEL_ID=1234567890123456789

# Optional - AI Behavior (system prompt now in system_prompt.txt file)
DEFAULT_MODEL=gpt-5-mini
DEFAULT_IMAGE_MODEL=dall-e-3

# Optional - Bot Behavior
INCLUDE_USERNAMES=true
REPLY_TO_MENTIONS=true
INCLUDE_NUM_CHATLINES=75

# Optional - Auto-Update and Restart Features
KEEP_UP_TO_DATE_WITH_GIT=true
QUIET_UPDATES=false

# Optional - OpenAI Function Calling
USER_CONTEXT_URL=https://your-server.com/api/user-context
```

### System Prompt File (`system_prompt.txt`)

Create a `system_prompt.txt` file in the project root to customize the AI's personality:

```txt
You are a helpful assistant. Today's date and time is [[CURRENT_DATE_AND_TIME]].

You are designed to be helpful, harmless, and honest. You can assist with a wide variety of tasks including:
- Answering questions and providing information
- Helping with writing and editing
- Explaining concepts and ideas
- Providing coding assistance
- Creative tasks like brainstorming

Please be concise but thorough in your responses. If you're unsure about something, say so rather than guessing.
```

**Key Features:**
- **Multi-line support**: Write complex, formatted prompts
- **Dynamic variables**: Use `[[CURRENT_DATE_AND_TIME]]` for current timestamp
- **Version control friendly**: Track changes to AI behavior
- **Fallback**: Uses default prompt if file is missing

## Discord Bot Setup

### Creating a Discord Application

1. **Visit Discord Developer Portal**
   - Go to https://discord.com/developers/applications
   - Click "New Application"
   - Give it a name and create

2. **Create Bot User**
   - Go to "Bot" section in left sidebar
   - Click "Add Bot"
   - Customize bot username and avatar

3. **Get Bot Token**
   - In Bot section, click "Copy" under Token
   - Add this to your `.env` file as `DISCORD_BOT_TOKEN`

4. **Configure Bot Permissions**
   - **Required Intents**:
     - ✅ MESSAGE CONTENT INTENT (required for reading messages)
     - ✅ SERVER MEMBERS INTENT (optional, for better user handling)
   - **Bot Permissions**:
     - ✅ Send Messages
     - ✅ Use Slash Commands
     - ✅ Read Message History
     - ✅ Attach Files
     - ✅ Embed Links

### Inviting Bot to Server

1. **Generate Invite URL**
   - Go to "OAuth2" → "URL Generator"
   - Select scopes: `bot` and `applications.commands`
   - Select permissions (see above)
   - Copy generated URL

2. **Invite to Server**
   - Open the generated URL
   - Select your server
   - Authorize the bot

## OpenAI API Setup

### Getting API Key

1. **Create OpenAI Account**
   - Visit https://platform.openai.com/
   - Sign up or log in

2. **Generate API Key**
   - Go to https://platform.openai.com/api-keys
   - Click "Create new secret key"
   - Copy the key immediately (it won't be shown again)
   - Add to `.env` file as `OPENAI_API_KEY`

3. **Set Up Billing**
   - Add payment method in OpenAI dashboard
   - Set usage limits to control costs
   - Monitor usage regularly

### Model Availability

Ensure your OpenAI account has access to the models you want to use:

- **GPT Models**: Usually available with API access
- **DALL-E Models**: May require separate access or higher tier
- **Image Models**: Check model availability in your region

## Advanced Configuration

### Per-Channel Settings

The bot maintains separate settings for each Discord channel:

- **Conversation History**: Each channel has its own conversation thread
- **AI Model**: Can be different per channel using `/model` command
- **System Prompt**: Can be customized per channel using `/systemprompt set`

### Runtime Configuration

Some settings can be changed without restarting the bot:

```bash
# Change AI model for current channel
/model gpt-5-nano

# Set custom personality for current channel
/systemprompt set You are a creative writing assistant

# Reset channel to default settings
/reset
/systemprompt reset
```

### Environment-Specific Configurations

#### Development Environment
```env
# Use separate bot for development
DISCORD_BOT_TOKEN=your_dev_bot_token
OPENAI_API_KEY=your_openai_key

# More verbose for debugging
SYSTEM_PROMPT="You are a helpful assistant in development mode"
DEFAULT_MODEL=gpt-5-nano  # Faster for testing
INCLUDE_NUM_CHATLINES=20    # Fewer lines for testing
```

#### Production Environment
```env
# Production bot token
DISCORD_BOT_TOKEN=your_prod_bot_token
OPENAI_API_KEY=your_openai_key

# Optimized for production
SYSTEM_PROMPT="You are a helpful assistant"
DEFAULT_MODEL=gpt-5-mini
INCLUDE_NUM_CHATLINES=100
INCLUDE_USERNAMES=true
REPLY_TO_MENTIONS=true
```

## Configuration Validation

The bot validates configuration on startup:

### Startup Checks
- ✅ Discord bot token format
- ✅ OpenAI API key format
- ✅ Model availability
- ✅ Discord connection
- ✅ Required permissions

### Error Messages
- **Invalid Discord Token**: Check token format and validity
- **Invalid OpenAI Key**: Verify API key and billing setup
- **Missing Permissions**: Check bot permissions in Discord server
- **Model Not Available**: Verify model access in OpenAI account

## Security Best Practices

### Token Security
- ✅ Never commit `.env` file to git
- ✅ Use different tokens for development and production
- ✅ Rotate tokens regularly
- ✅ Restrict file permissions: `chmod 600 .env`

### API Key Management
- ✅ Set usage limits in OpenAI dashboard
- ✅ Monitor API usage regularly
- ✅ Use separate keys for different environments
- ✅ Revoke unused keys

### Discord Security
- ✅ Use minimal required permissions
- ✅ Restrict bot to specific channels if needed
- ✅ Monitor bot activity logs
- ✅ Remove bot from unused servers

## Troubleshooting Configuration

### Common Issues

1. **Bot Not Responding**
   - Check `DISCORD_BOT_TOKEN` is correct
   - Verify MESSAGE CONTENT INTENT is enabled
   - Ensure bot has required permissions

2. **OpenAI Errors**
   - Verify `OPENAI_API_KEY` is valid
   - Check billing and usage limits
   - Confirm model availability

3. **Permission Errors**
   - Check bot permissions in Discord server
   - Verify bot role hierarchy
   - Ensure channel-specific permissions

4. **Configuration Not Loading**
   - Check `.env` file exists and is readable
   - Verify environment variable names
   - Check for syntax errors in `.env`

### Debug Commands

```bash
# Check environment variables
env | grep DISCORD
env | grep OPENAI

# Test bot token (be careful with this)
# Don't run this in production or shared environments
python -c "import os; print('Token length:', len(os.getenv('DISCORD_BOT_TOKEN', '')))"

# Validate configuration
python -c "from src.config import *; print('Config loaded successfully')"
```

For more troubleshooting help, see [troubleshooting.md](troubleshooting.md).
