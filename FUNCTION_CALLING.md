# OpenAI Function Calling Feature

This document describes the OpenAI function calling feature that allows the bot to dynamically fetch user context when needed.

## Overview

The function calling feature enables the bot to intelligently request additional context about users when OpenAI determines it would be helpful for generating personalized responses. This avoids sending large context data on every request while still providing rich, personalized interactions.

## Configuration

### Environment Variables

Add the following to your `.env` file:

```bash
USER_CONTEXT_URL=https://your-server.com/api/user-context
```

If `USER_CONTEXT_URL` is not set or empty, the bot will use the legacy API without function calling.

### System Prompt

The system prompt should include instructions for when to use the function. Example addition to `system_prompt.txt`:

```
If the user asks about themselves, call function get_user_context() to fetch old 90s IRC quotes from our old channel (excuse the 90s behavior, don't judge just joke). Then use those quotes to roast them jokingly. You may have to fuzzy match some nicks like mem0ut and memout and jobez and jobe and jobeus, etc.
```

## How It Works

1. **Normal Operation**: When `USER_CONTEXT_URL` is not configured, the bot uses the legacy API
2. **Function Calling Enabled**: When `USER_CONTEXT_URL` is configured, the bot uses OpenAI's function calling API
3. **Dynamic Context Fetching**: 
   - OpenAI determines when user context would be helpful
   - Bot calls the `get_user_context()` function
   - HTTP GET request is made to the configured URL
   - Context data is injected into the conversation
   - OpenAI generates a response using the context

## Function Definition

The bot automatically defines this function for OpenAI:

```json
{
  "name": "get_user_context",
  "description": "Fetch historical IRC quotes and context about the user for personalized responses",
  "parameters": {
    "type": "object",
    "properties": {},
    "required": []
  }
}
```

## Context API Requirements

Your context API endpoint should:

- Accept HTTP GET requests
- Return plain text content (IRC logs, user history, etc.)
- Handle timeouts gracefully (10 second timeout)
- Return appropriate HTTP status codes

Example response:
```
<jobe> hello world, this is a test message from 1999
<memout> jobe: you're such a n00b
<jobe> memout: at least I'm not using AOL
```

## Error Handling

The bot gracefully handles various error conditions:

- **Timeout**: Returns "User context fetch timed out."
- **HTTP Errors**: Returns "User context fetch failed with HTTP {status_code}."
- **Network Errors**: Returns "User context fetch failed: {error_message}"
- **No URL Configured**: Returns "User context URL not configured."

## User Experience

From the Discord user's perspective:
- No visible difference in bot behavior
- Typing indicator shows during the entire process
- Only the final response is sent to Discord
- Context fetching happens transparently

## Benefits

- **Efficient**: Only fetches context when OpenAI determines it's needed
- **Intelligent**: OpenAI decides when personalization would be helpful
- **Scalable**: Avoids sending large context on every request
- **Transparent**: Users don't see the function calling process
- **Fallback**: Gracefully degrades when context is unavailable

## Example Flow

1. User: "Tell me about myself"
2. OpenAI: Determines context would be helpful, calls `get_user_context()`
3. Bot: Fetches context from configured URL
4. Bot: Sends context back to OpenAI as function result
5. OpenAI: Generates personalized response using the context
6. Bot: Sends final response to Discord user

## Dependencies

- `httpx`: Added for async HTTP requests to context API
- OpenAI function calling support (uses standard `chat.completions.create` API)

## Backward Compatibility

- Existing functionality is preserved when `USER_CONTEXT_URL` is not set
- All existing tests continue to pass
- Legacy API is used as fallback
