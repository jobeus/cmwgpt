# Auto-Update Feature

The Discord bot includes an auto-update feature that automatically monitors for new commits in the git repository and restarts the bot to apply updates. This feature ensures your bot stays up-to-date with the latest code changes without manual intervention.

## Features

- **Automatic Git Monitoring**: Periodically checks for new commits on the current branch
- **State Persistence**: Saves and restores bot state (conversations, models, system prompts) across restarts
- **Manual Restart Command**: `/restart` command for administrators to trigger updates manually
- **Update Announcements**: Automatically announces updates to channels where the bot has been used
- **Graceful Shutdown**: Properly saves state before restarting
- **Error Handling**: Robust error handling with automatic retry and failure limits
- **Security**: Secure temporary file handling with restrictive permissions

## Configuration

### Environment Variables

Add the following to your `.env` file:

```bash
# Enable/disable auto-update feature
KEEP_UP_TO_DATE_WITH_GIT=True
```

Set to `False` to disable the auto-update feature entirely.

### Requirements

1. **Git Repository**: The bot must be running from within a git repository
2. **Origin Remote**: The repository must have an `origin` remote configured
3. **Clean Working Directory**: Uncommitted changes may prevent git pull operations

## How It Works

### Automatic Updates

1. **Background Monitoring**: A background thread runs every 5 minutes (configurable)
2. **Git Fetch**: Fetches latest changes from `origin`
3. **Commit Check**: Compares local HEAD with `origin/<current_branch>`
4. **State Saving**: If new commits are found, saves current bot state to a secure temporary file
5. **Git Pull**: Performs `git pull origin` to update the code
6. **Restart**: Gracefully shuts down and exits with code 42 (restart signal)
7. **State Restoration**: On startup, automatically restores saved state
8. **Update Announcement**: Sends update notifications to active channels with git commit info

### Manual Restart

Use the `/restart` command to manually trigger an update:

```
/restart
```

**Requirements:**
- Administrator permissions in the Discord server
- The command performs the same process as automatic updates

## Update Announcements

After a successful restart, the bot automatically announces the update to all channels where it has been used. The announcement includes:

- **Update Type**: Whether it was an automatic update or manual restart
- **Git Commit**: The current git commit SHA (short form)
- **Recent Changes**: Brief summary of recent commits (up to 3)
- **Ready Message**: Confirmation that the bot is ready to assist

### Example Announcement

```
🤖 **Bot Updated** (auto-update)
📝 Now running commit `abc12345`

**Recent changes:**
• Fix memory leak in conversation handling
• Add support for new OpenAI models
• Improve error handling for Discord API

*Ready to assist! Use `/chat` or mention me to continue.*
```

### Channel Tracking

The bot automatically tracks channels where it has been used by monitoring:
- `/chat` command usage
- Bot mentions and responses
- `/model` and `/systemprompt` command usage

Only channels with recent bot activity receive update announcements, reducing spam in unused channels.

## State Persistence

The bot automatically saves and restores the following state across restarts:

- **Conversations**: All channel conversation histories
- **Models**: Per-channel model settings
- **System Prompts**: Custom system prompts per channel
- **Active Channels**: List of channels where the bot has been used (for announcements)

### Temporary Files

- **Location**: `/tmp/cmwgpt_state_backup_{timestamp}_{pid}.json`
- **Permissions**: 600 (read/write for owner only)
- **Cleanup**: Automatically removed after successful restoration
- **Security**: Unique filenames prevent conflicts and unauthorized access

## Process Management

The auto-update feature works with various process managers:

### systemd

```ini
[Unit]
Description=AI Discord Bot
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/cmwgpt
Environment=PATH=/path/to/cmwgpt/venv/bin
ExecStart=/path/to/cmwgpt/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### PM2

```javascript
module.exports = {
  apps: [{
    name: 'discord-bot',
    script: 'python',
    args: 'main.py',
    cwd: '/path/to/cmwgpt',
    interpreter: '/path/to/cmwgpt/venv/bin/python',
    restart_delay: 10000,
    max_restarts: 10
  }]
}
```

### Docker

The bot exits with code 42 on restart, which most process managers interpret as a signal to restart the container/process.

## Error Handling

### Git Operation Failures

- **Consecutive Failure Limit**: 5 failures before disabling auto-update
- **Timeout Protection**: Git operations have 30-60 second timeouts
- **Graceful Degradation**: Bot continues running even if auto-update fails

### State Persistence Failures

- **Fallback**: Bot starts with fresh state if restoration fails
- **Logging**: All failures are logged for debugging
- **Cleanup**: Temporary files are cleaned up even on failure

## Monitoring and Debugging

### Log Messages

The auto-update feature provides detailed logging:

```
INFO:discord_bot: Auto-update service started (check interval: 300s)
INFO:auto_update_service: Found 2 new commits on origin/main
INFO:auto_update_service: New commits detected, triggering restart
INFO:state_service: State saved to temporary file: /tmp/cmwgpt_state_backup_1234567890_12345.json
INFO:restart_handler: Starting bot restart process
INFO:restart_handler: Git pull completed successfully
INFO:discord_bot: Successfully restored state from previous restart
INFO:announcement_service: Announcing update to 3 channels: abc12345
INFO:announcement_service: Update announcements sent: 3 successful, 0 failed
```

### Status Checking

The auto-update service provides status information:

```python
from src.services.auto_update_service import auto_update_service

status = auto_update_service.get_status()
print(status)
# {
#   'enabled': True,
#   'running': True,
#   'check_interval': 300,
#   'consecutive_failures': 0,
#   'max_failures': 5,
#   'last_known_commit': 'abc123...',
#   'is_git_repo': True
# }
```

## Security Considerations

### File Permissions

- Temporary state files use 600 permissions (owner read/write only)
- Files are created in `/tmp/` with unique names to prevent conflicts
- Automatic cleanup prevents sensitive data from persisting

### Git Operations

- Only performs `git fetch` and `git pull` operations
- Validates git repository state before operations
- Does not modify git configuration or perform destructive operations

### Process Isolation

- Auto-update runs in a separate background thread
- Git operations are isolated with timeouts
- Failures in auto-update don't affect bot functionality

## Troubleshooting

### Auto-Update Not Working

1. **Check Configuration**: Ensure `KEEP_UP_TO_DATE_WITH_GIT=True`
2. **Verify Git Repository**: Confirm you're in a git repository with `git status`
3. **Check Origin Remote**: Verify `git remote -v` shows origin
4. **Review Logs**: Look for auto-update related log messages
5. **Test Manual Restart**: Try `/restart` command to test the mechanism

### State Not Restored

1. **Check Permissions**: Ensure bot has write access to `/tmp/`
2. **Review Logs**: Look for state saving/loading error messages
3. **Verify Disk Space**: Ensure sufficient space in `/tmp/`

### Git Pull Failures

1. **Uncommitted Changes**: Commit or stash local changes
2. **Merge Conflicts**: Resolve any merge conflicts manually
3. **Network Issues**: Check internet connectivity and git remote access
4. **Permissions**: Ensure bot user has git repository access

## Best Practices

1. **Test in Development**: Test auto-update in a development environment first
2. **Monitor Logs**: Regularly check logs for auto-update activity
3. **Backup Strategy**: Consider additional backup strategies for critical data
4. **Update Frequency**: Balance update frequency with stability needs
5. **Process Manager**: Use a reliable process manager for production deployments
