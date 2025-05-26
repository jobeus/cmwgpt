# 🏗️ Architecture

## Overview

The AI Discord Bot follows a clean, modular architecture with proper separation of concerns. This design ensures maintainability, testability, and extensibility.

## Project Structure

```
src/
├── bot/
│   ├── client.py              # Main Discord bot client and event handling
│   ├── commands/
│   │   ├── chat.py           # Chat-related commands (/chat, /reset)
│   │   ├── image.py          # Image generation (/draw)
│   │   └── system.py         # System commands (/model, /systemprompt)
│   └── handlers/
│       └── mention.py        # Bot mention handling and context preparation
├── services/
│   ├── openai_service.py     # OpenAI API integration
│   ├── message_service.py    # Message formatting and sending
│   ├── paste_service.py      # Paste service integration (for long messages)
│   ├── queue_service.py      # FIFO command queue management
│   └── state_service.py      # Bot state management (conversations, models, prompts)
├── utils/
│   ├── discord_helper.py     # Discord utilities and helper functions
│   └── pasters.py           # Legacy paste service compatibility
└── config.py                 # Configuration management and environment variables
main.py                       # Application entry point
```

## Core Components

### 1. Bot Client (`src/bot/client.py`)

The main Discord bot client that:
- Configures Discord intents and permissions
- Sets up event handlers for messages and bot lifecycle
- Registers slash commands from command modules
- Handles bot mentions and routes them to the mention handler

### 2. Command System (`src/bot/commands/`)

Modular command system with separate files for different functionality:

#### Chat Commands (`chat.py`)
- `/chat` - Start or continue conversations with AI
- `/reset` - Clear conversation history for the current channel
- Handles image attachments and conversation context

#### Image Commands (`image.py`)
- `/draw` - Generate images using DALL-E models
- Supports multiple AI models (DALL-E 2, DALL-E 3, GPT-Image-1)
- Image editing capabilities

#### System Commands (`system.py`)
- `/model` - View or change AI model per channel
- `/systemprompt` group - Manage channel-specific AI personalities
  - `set` - Set custom system prompt
  - `view` - Display current system prompt
  - `reset` - Reset to default system prompt

### 3. Mention Handler (`src/bot/handlers/mention.py`)

Sophisticated mention handling that:
- Analyzes recent channel history for context
- Prepares conversation context for OpenAI
- Provides contextual responses based on ongoing discussions
- Supports both immediate and queued processing

### 4. Service Layer (`src/services/`)

#### OpenAI Service (`openai_service.py`)
- Centralized OpenAI API integration
- Handles chat completions and image generation
- Error handling and retry logic
- Model-agnostic interface

#### Message Service (`message_service.py`)
- Discord message formatting and sending
- Handles Discord's 2000 character limit
- Integration with paste services for long responses
- Typing indicators and user feedback

#### State Service (`state_service.py`)
- In-memory state management for:
  - Per-channel conversation histories
  - Per-channel AI model selections
  - Per-channel system prompts
- Thread-safe operations

#### Queue Service (`queue_service.py`)
- FIFO command processing queue
- Prevents race conditions between concurrent commands
- Handles both slash commands and mentions
- Configurable queue size and timeout handling

#### Paste Service (`paste_service.py`)
- Integration with paste.rs for long responses
- Automatic fallback when Discord message limits are exceeded
- Error handling for paste service failures

## Key Architectural Patterns

### 1. Separation of Concerns
- **Commands**: Handle Discord interactions and user input
- **Services**: Implement business logic and external API integration
- **Handlers**: Process specific event types (mentions, etc.)
- **Utils**: Provide reusable helper functions

### 2. Dependency Injection
- Services are injected into commands and handlers
- Enables easy testing with mock services
- Reduces coupling between components

### 3. Event-Driven Architecture
- Discord events trigger appropriate handlers
- Asynchronous processing throughout
- Queue-based command processing prevents race conditions

### 4. State Management
- Centralized state service manages all bot state
- Per-channel isolation of conversations and settings
- In-memory storage with potential for persistence layer

## Conversation Modes

### Separate Conversations (`/chat`)
- Each channel maintains its own conversation history
- Messages are added to a persistent conversation thread
- Context is preserved across multiple interactions
- Ideal for extended discussions and focused conversations

### Contextual Responses (`@mention`)
- Bot analyzes recent channel history (configurable number of messages)
- Provides one-off responses based on current context
- Does not maintain persistent conversation state
- Perfect for getting AI input on ongoing discussions

## Error Handling Strategy

### Robust Error Handling
- Comprehensive try-catch blocks around all external API calls
- User-friendly error messages for common failures
- Detailed logging for debugging and monitoring
- Graceful degradation when services are unavailable

### Queue Management
- Commands are queued to prevent race conditions
- Queue overflow handling with user feedback
- Timeout handling for long-running operations
- Fallback to immediate processing when queue is full

## Scalability Considerations

### Current Architecture
- In-memory state storage (suitable for single-instance deployment)
- Asynchronous processing throughout
- Modular design allows for easy horizontal scaling

### Future Enhancements
- Database persistence for conversation history
- Redis for distributed state management
- Microservice decomposition for large-scale deployment
- Load balancing for multiple bot instances

## Testing Strategy

### Unit Testing
- Comprehensive test suite covering all major components
- Mock services for external dependencies
- Test coverage for error conditions and edge cases

### Integration Testing
- End-to-end command testing
- Discord API integration testing
- OpenAI API integration testing

## Security Considerations

### API Key Management
- Environment variable configuration
- No hardcoded secrets in source code
- Secure token handling

### Input Validation
- Discord message content validation
- OpenAI API input sanitization
- File upload validation for image attachments

### Rate Limiting
- Respect Discord API rate limits
- OpenAI API rate limiting and retry logic
- Queue-based processing prevents API flooding
