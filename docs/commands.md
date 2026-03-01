# 📖 Complete Commands Reference

## Overview

The AI Discord Bot supports two main interaction modes:
1. **Slash Commands** (`/command`) - Structured commands with parameters
2. **Mentions** (`@BotName`) - Natural language interactions with context

## Chat Commands

### `/chat [message] [attachment]`

Start or continue a conversation with the AI.

**Parameters:**
- `message` (required): Your text message to the AI
- `attachment` (optional): Image file for AI analysis

**Features:**
- Maintains conversation history per channel
- Supports image analysis (JPG, PNG, GIF, WebP)
- Auto-uploads long responses to paste.rs
- Preserves context across multiple interactions

**Examples:**
```
/chat Hello, can you help me with Python programming?
/chat Explain this code [attach: code_screenshot.png]
/chat What's the weather like? [attach: weather_photo.jpg]
```

**Behavior:**
- Each Discord channel maintains its own conversation thread
- Messages are added to persistent conversation history
- Bot remembers previous context within the same channel
- Images are analyzed using OpenAI's vision capabilities

### `/reset`

Clear conversation history for the current channel.

**Parameters:** None

**Effects:**
- Clears all conversation history for the current channel
- Resets to default or custom system prompt
- Maintains current model selection
- Does not affect other channels

**Example:**
```
/reset
```

**Use Cases:**
- Start fresh conversation
- Clear sensitive information from history
- Reset after conversation goes off-topic
- Troubleshoot conversation issues

## Image Commands

### `/draw [prompt] [model]`

Generate images using AI.

**Parameters:**
- `prompt` (required): Description of the image you want
- `model` (optional): AI model to use

**Available Models:**
- `seedream` - Standard Image model
- *(If Runpod API is configured)*: `z-image`, `wan-2.6`, `pruna`, `qwen`, `flux`

**Examples:**
```
/draw A sunset over mountains with a lake
/draw A cat wearing a space helmet model:seedream
```

### `/edit [prompt] [edit_image] [image2] [image3] [image4] [model]`

Edit or modify existing images using AI.

**Parameters:**
- `prompt` (required): Instructions on how to edit the image
- `edit_image` (required): The primary image to edit
- `image2` to `image4` (optional): Additional reference images
- `model` (optional): AI model to use

**Available Models:**
- `seedream` - Standard multi-image edit
- *(If Runpod API is configured)*: `qwen`, `pruna` (supports up to 4 images)

**Examples:**
```
/edit Make this image more colorful [attach: original.jpg]
/edit Combine these images [attach: one.jpg] image2:[attach: two.jpg] model:seedream
```

### `/drawmodel [model]`

View or set the default image generation model for the current channel.

**Parameters:**
- `model` (optional): Name of the model to set as default. If omitted, displays current model.

### `/editmodel [model]`

View or set the default image editing model for the current channel.

**Parameters:**
- `model` (optional): Name of the model to set as default. If omitted, displays current model.

## System Management Commands

### `/help`

Get help with bot commands privately.

**Parameters:** None

**Behavior:**
- Opens an ephemeral message with a comprehensive commands list.
- Only visible to the user who typed the command.

### `/model [model_name]`

View or change the AI model for the current channel.

**Parameters:**
- `model_name` (optional): Model to switch to

**Available Models:**
- `google/gemini-2.5-flash` - Fast and capable Google model
- `bytedance-seed/seed-2.0-mini` - Efficient Seed model
- `minimax/minimax-m2-her` - MiniMax M2 model
- `qwen/qwen3.5-flash-02-23` - Qwen 3.5 Flash model
- `anthropic/claude-haiku-4.5` - Fast and efficient latest model (recommended, with web search)

**Examples:**
```
/model                            # View current model
/model anthropic/claude-haiku-4.5 # Switch to Claude Haiku
/model google/gemini-2.5-flash    # Switch to Gemini Flash
```

**Behavior:**
- Model selection is per-channel
- Setting persists until changed
- Affects all future `/chat` commands in that channel
- Does not affect conversation history

### `/systemprompt` Command Group

Manage channel-specific AI personalities and behavior.

#### `/systemprompt set [prompt]`

Set a custom system prompt for the current channel.

**Parameters:**
- `prompt` (required): The personality/behavior instructions for the AI

**Examples:**
```
/systemprompt set You are a helpful coding assistant specializing in Python
/systemprompt set You are a creative writing assistant who helps with storytelling
/systemprompt set You are a technical documentation specialist
/systemprompt set Act like a friendly teacher explaining complex topics simply
```

**Effects:**
- Applies immediately to current conversation
- Persists for all future conversations in this channel
- Overrides default system prompt
- Can be reset using `/systemprompt reset`

#### `/systemprompt view`

Display the current system prompt for the channel.

**Parameters:** None

**Example:**
```
/systemprompt view
```

**Output:**
Shows the current system prompt, whether it's the default or a custom one.

#### `/systemprompt reset`

Reset to the default system prompt.

**Parameters:** None

**Example:**
```
/systemprompt reset
```

**Effects:**
- Removes custom system prompt
- Returns to default system prompt from configuration
- Applies immediately to current conversation
- Affects all future conversations in this channel

### `/restart`

Restart the bot with latest updates (administrator only).

**Parameters:** None

**Requirements:**
- Administrator permissions in the Discord server
- Auto-update feature must be enabled (`KEEP_UP_TO_DATE_WITH_GIT=true`)

**Example:**
```
/restart
```

**Behavior:**
- Saves current bot state (conversations, models, system prompts)
- Performs `git pull` to update code
- Gracefully shuts down and restarts the bot
- Restores saved state after restart
- Announces update to active channels (unless `QUIET_UPDATES=true`)

**Use Cases:**
- Apply code updates immediately
- Restart bot after configuration changes
- Recover from stuck or unresponsive state
- Test auto-update functionality

**Note:** This command triggers the same process as automatic updates. See [Auto-Update Documentation](auto-update.md) for more details.

## Mention Interactions

### `@BotName [message]`

Mention the bot for contextual responses based on recent channel activity.

**Format:**
```
@YourBot what do you think about this?
@YourBot can you help with the issue mentioned above?
@YourBot summarize the last few messages
```

**Behavior:**
- Analyzes recent channel history (configurable, default: 100 messages)
- Provides context-aware responses
- Does not maintain persistent conversation state
- Uses current channel's system prompt and model settings

**Context Analysis:**
- Reads recent messages for context
- Understands user mentions and references
- Provides relevant responses based on ongoing discussions
- Includes user legend for better understanding

**Use Cases:**
- Get AI input on ongoing discussions
- Ask questions about recent conversation
- Request summaries or clarifications
- Participate in group conversations

## Command Behavior Details

### Queue Processing

All commands are processed through a FIFO queue system:
- Prevents race conditions between concurrent commands
- Ensures commands are processed in order
- Handles queue overflow gracefully
- Provides user feedback for queue status

### Error Handling

Comprehensive error handling for all commands:
- User-friendly error messages
- Automatic retry for transient failures
- Graceful degradation when services are unavailable
- Detailed logging for debugging

### Response Handling

Smart response management:
- **Short responses**: Sent directly to Discord
- **Long responses**: Automatically uploaded to paste.rs with link
- **Images**: Embedded directly in Discord
- **Errors**: Clear, actionable error messages

### Typing Indicators

Visual feedback during processing:
- Bot shows "typing" indicator while processing
- Indicates active work on user requests
- Helps users understand processing time
- Automatic timeout handling

## Advanced Usage Patterns

### Conversation Workflows

1. **Extended Discussions**:
   ```
   /chat Let's discuss Python best practices
   /chat What about error handling?
   /chat Can you show me an example?
   ```

2. **Image Analysis Workflows**:
   ```
   /chat Analyze this code screenshot [attach: code.png]
   /chat What improvements would you suggest?
   /draw Create a diagram showing the improved architecture
   ```

3. **Channel-Specific Assistants**:
   ```
   # In #coding channel
   /systemprompt set You are a senior software engineer

   # In #creative-writing channel
   /systemprompt set You are a creative writing mentor
   ```

### Multi-Model Strategies

- **Quick responses**: Use `google/gemini-2.5-flash` for fast, simple queries
- **Web-aware reasoning**: Use `anthropic/claude-haiku-4.5` for current information and detailed analysis
- **Alternative perspectives**: Switch between `qwen` and `minimax` models for varied responses

### Context Management

- **Separate conversations**: Use `/chat` for focused discussions
- **Contextual input**: Use `@mentions` for group participation
- **Fresh starts**: Use `/reset` when changing topics
- **Personality changes**: Use `/systemprompt set` for different contexts

## Permissions and Limitations

### Required Permissions

The bot needs these Discord permissions:
- Send Messages
- Use Slash Commands
- Read Message History
- Attach Files
- Embed Links

### Rate Limiting

- Discord API rate limits apply
- OpenAI API rate limits apply
- Queue system prevents overwhelming APIs
- Automatic retry with exponential backoff

### Content Limitations

- Discord message limit: 2000 characters (auto-paste for longer)
- Image size limits: Discord attachment limits apply
- OpenAI content policies apply to all interactions
- Token limits vary by model

## Troubleshooting Commands

### Common Issues

1. **Command not responding**:
   - Check bot permissions
   - Verify bot is online
   - Try `/reset` to clear any stuck state

2. **Image commands failing**:
   - Check image format (JPG, PNG, GIF, WebP supported)
   - Verify image size is within Discord limits
   - Ensure OpenAI API key has image access

3. **Conversation context issues**:
   - Use `/reset` to clear conversation history
   - Check system prompt with `/systemprompt view`
   - Verify model selection with `/model`

### Debug Information

For support, provide:
- Command used
- Error message received
- Channel where issue occurred
- Approximate time of issue

For more troubleshooting help, see [troubleshooting.md](troubleshooting.md).
