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
│   ├── openai_service.py     # OpenAI API integration with function calling support
│   ├── message_service.py    # Message formatting and sending with error handling
│   ├── paste_service.py      # Paste service integration (for long messages)
│   ├── queue_service.py      # FIFO command queue management
│   ├── state_service.py      # Bot state management (conversations, models, prompts)
│   ├── auto_update_service.py # Automatic git-based updates and monitoring
│   ├── restart_handler.py    # Graceful restart and shutdown with state persistence
│   └── announcement_service.py # Update announcements and channel tracking
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
- `/draw` - Generate images using AI models
- Supports multiple AI models (Seedream default, Runpod models)
- Image editing capabilities

#### System Commands (`system.py`)
- `/model` - View or change AI model per channel
- `/systemprompt` group - Manage channel-specific AI personalities
  - `set` - Set custom system prompt
  - `view` - Display current system prompt
  - `reset` - Reset to default system prompt
- `/restart` - Manual restart with updates (administrator only)

### 3. Mention Handler (`src/bot/handlers/mention.py`)

Sophisticated mention handling that:
- Analyzes recent channel history for context
- Prepares conversation context for OpenAI
- Provides contextual responses based on ongoing discussions
- Supports both immediate and queued processing

### 4. Service Layer (`src/services/`)

#### OpenAI Service (`openai_service.py`)
- Centralized OpenAI API integration with function calling support
- Handles chat completions, image generation, and dynamic context fetching
- Comprehensive error handling and retry logic with exponential backoff
- Model-agnostic interface supporting multiple OpenAI models
- Function calling implementation for user context API integration
- **Optimized conversation continuity**: Streamlined response processing with consolidated response ID tracking and text extraction
- **Consolidated API operations**: Single comprehensive method handles all response types with reduced code duplication

#### Message Service (`message_service.py`)
- Discord message formatting and sending
- Handles Discord's 2000 character limit
- Integration with paste services for long responses
- Typing indicators and user feedback

#### State Service (`state_service.py`)
- **Optimized thread-safe in-memory state management** with consolidated data structures:
  - Per-channel conversation histories
  - Per-channel AI model selections
  - Per-channel system prompts
  - **Per-channel OpenAI response IDs** for conversation continuity
  - Active channel tracking for announcements
  - Git SHA tracking for update detection
- **Streamlined state persistence** with single consolidated temporary file operations
- **Batch operations** for efficient multi-field updates and context retrieval
- Automatic state restoration on startup with cleanup

#### Queue Service (`queue_service.py`)
- FIFO command processing queue
- Prevents race conditions between concurrent commands
- Handles both slash commands and mentions
- Configurable queue size and timeout handling

#### Paste Service (`paste_service.py`)
- Integration with paste.rs for long responses
- Automatic fallback when Discord message limits are exceeded
- Error handling for paste service failures

#### Auto-Update Service (`auto_update_service.py`)
- Background git repository monitoring
- Automatic detection of new commits
- Triggered restart process with state preservation
- Configurable check intervals and failure limits
- Manual restart capability through `/restart` command

#### Restart Handler (`restart_handler.py`)
- Graceful bot restart with state persistence
- Git pull operations for code updates
- Secure temporary file handling for state backup
- Signal-based restart coordination (exit code 42)
- Graceful shutdown handling for external termination

#### Announcement Service (`announcement_service.py`)
- Post-restart update announcements to active channels
- Git commit tracking and changelog generation
- Intelligent duplicate announcement prevention
- Paste service integration for long changelogs
- Configurable quiet mode for silent updates

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

## Advanced Features

### Auto-Update System
- Background git repository monitoring with configurable intervals
- Automatic restart triggering when new commits are detected
- State persistence across restarts using secure temporary files
- Manual restart capability through Discord `/restart` command
- Intelligent update announcements with git commit information

### State Persistence
- Thread-safe in-memory state management for all bot data
- Automatic state backup before restarts or shutdowns
- Secure temporary file handling with restrictive permissions
- State restoration on startup with automatic cleanup
- Per-channel isolation of conversations, models, and system prompts

## Error Handling Strategy

### Robust Error Handling
- Comprehensive try-catch blocks around all external API calls
- User-friendly error messages for common failures
- Detailed logging for debugging and monitoring
- Graceful degradation when services are unavailable
- Automatic retry with exponential backoff for transient failures
- Rate limiting protection for Discord and OpenAI APIs

### OpenAI API Error Handling
- Comprehensive error handling for all OpenAI API calls
- Automatic retry for rate limits and temporary failures
- Graceful handling of quota exceeded and invalid API key errors
- Function calling error handling with fallback to legacy API
- Timeout protection for long-running requests

### Discord API Error Handling
- Rate limiting detection and automatic retry
- Permission error handling with clear user feedback
- Message length handling with automatic paste service fallback
- Connection error recovery and reconnection logic
- Interaction timeout handling (3-second Discord limit)

### Queue Management
- Commands are queued to prevent race conditions
- Queue overflow handling with user feedback
- Timeout handling for long-running operations
- Fallback to immediate processing when queue is full
- FIFO processing ensures command order preservation

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

## Conversation Continuity

The bot implements conversation continuity using OpenAI's `previous_response_id` parameter to maintain context across interactions within each Discord channel.

### How It Works

1. **Response ID Tracking**: When the OpenAI API returns a response, the bot extracts the `id` field from the response object and stores it associated with the specific Discord channel.

2. **Previous Response ID Parameter**: For subsequent API calls in the same channel, the bot includes the `previous_response_id` parameter referencing the most recent response ID for that channel.

3. **Channel Isolation**: Response IDs are tracked separately per Discord channel, ensuring conversations in different channels don't interfere with each other.

4. **First Messages**: For the first message in a new conversation (when no previous response exists for that channel), the `previous_response_id` parameter is omitted.

5. **Persistence**: Response IDs are included in the bot's state persistence system, so conversation continuity is maintained across bot restarts.

### Benefits

- **Improved Context**: OpenAI can better understand the flow of conversation within each channel
- **More Coherent Responses**: The AI maintains awareness of previous interactions in the same channel
- **Channel-Specific Context**: Each Discord channel maintains its own conversation thread
- **Restart Resilience**: Conversation continuity persists through bot restarts and updates

### Implementation Details

- Response IDs are stored in the `StateService` with thread-safe access using consolidated data structures
- The `OpenAIService` automatically handles response ID extraction and storage in optimized single operations
- All OpenAI API calls (including tool calling follow-ups) include the previous response ID when available
- Response IDs are persisted to temporary files in `/tmp/` during bot restarts using streamlined file operations

## Performance Optimizations

The conversation continuity implementation has been optimized for performance and maintainability:

### Consolidated Data Structures
- **Single channel data structure**: All per-channel data (conversations, models, system prompts, response IDs) stored in one consolidated dictionary
- **Reduced lock contention**: Two locks instead of six (channel data lock + global data lock)
- **Efficient batch operations**: Methods like `get_channel_context()` and `update_channel_context()` handle multiple fields in single operations

### Streamlined State Persistence
- **Single file operations**: Consolidated temporary file creation instead of multiple file handling
- **Optimized serialization**: Efficient data extraction from consolidated structures
- **Reduced I/O overhead**: Fewer file system operations during save/load cycles

### Optimized OpenAI Integration
- **Consolidated response handling**: Single method `_extract_response_text_and_store_id()` handles both text extraction and ID storage
- **Unified API parameter preparation**: `_prepare_api_params()` eliminates duplicate parameter setup code
- **Comprehensive response processing**: `_handle_openai_response_with_continuity()` manages entire response flow in one operation
- **Eliminated code duplication**: Removed redundant response parsing logic across different code paths

### Benefits
- **Reduced memory footprint**: Consolidated data structures use less memory
- **Improved performance**: Fewer function calls and lock acquisitions
- **Better maintainability**: Less code duplication and clearer separation of concerns
- **Enhanced reliability**: Atomic operations reduce race conditions
- Queue-based processing prevents API flooding
