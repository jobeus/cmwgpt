# 🔧 Troubleshooting Guide

## Common Issues and Solutions

### Bot Not Responding

#### Symptoms
- Slash commands don't appear
- Bot doesn't respond to `/chat` or other commands
- No reaction to @mentions

#### Solutions

1. **Check Bot Status**
   ```bash
   # Check if bot process is running
   ps aux | grep python | grep main.py
   
   # Check systemd service (if using systemd)
   sudo systemctl status discord-bot
   ```

2. **Verify Discord Token**
   - Ensure `DISCORD_BOT_TOKEN` is correct in `.env`
   - Check token hasn't expired or been regenerated
   - Verify no extra spaces or characters

3. **Check Discord Permissions**
   - Bot needs "Send Messages" permission
   - Bot needs "Use Slash Commands" permission
   - Bot needs "Read Message History" permission
   - Verify bot role is above other roles if needed

4. **Verify Intents**
   - MESSAGE CONTENT INTENT must be enabled in Discord Developer Portal
   - SERVER MEMBERS INTENT recommended for better functionality

#### Quick Fix
```bash
# Restart the bot
sudo systemctl restart discord-bot
# or
pm2 restart discord-bot
```

### OpenAI API Errors

#### Symptoms
- "OpenAI API error" messages
- Commands timeout or fail
- Image generation not working

#### Common Error Messages and Solutions

1. **"Invalid API Key"**
   - Verify `OPENAI_API_KEY` in `.env` file
   - Check for typos or extra characters
   - Ensure API key hasn't been revoked

2. **"Insufficient Credits"**
   - Check OpenAI billing dashboard
   - Add payment method or increase limits
   - Monitor usage to avoid overages

3. **"Rate Limit Exceeded"**
   - Wait for rate limit to reset (usually 1 minute)
   - Consider upgrading OpenAI plan for higher limits
   - Reduce concurrent usage

4. **"Model Not Available"**
   - Check if model exists and is accessible
   - Verify your OpenAI plan includes the model
   - Try switching to a different model: `/model gpt-4.1-nano`

#### Debug OpenAI Issues
```bash
# Test API key manually
curl -H "Authorization: Bearer $OPENAI_API_KEY" \
     https://api.openai.com/v1/models
```

### Slash Commands Not Appearing

#### Symptoms
- `/chat`, `/draw`, etc. don't show up in Discord
- Commands were working before but disappeared

#### Solutions

1. **Re-invite Bot with Correct Permissions**
   - Go to Discord Developer Portal
   - OAuth2 → URL Generator
   - Select `bot` and `applications.commands` scopes
   - Select required permissions
   - Use new invite URL

2. **Wait for Command Sync**
   - Discord can take up to 1 hour to sync slash commands
   - Try in a different server to test
   - Restart Discord client

3. **Check Bot Permissions**
   - Bot needs "Use Slash Commands" permission
   - Check server-wide and channel-specific permissions

#### Force Command Refresh
```bash
# Restart bot to re-register commands
sudo systemctl restart discord-bot
```

### Image Commands Not Working

#### Symptoms
- `/draw` command fails
- Image analysis in `/chat` doesn't work
- "Image processing error" messages

#### Solutions

1. **Check Image Format**
   - Supported: JPG, PNG, GIF, WebP
   - File size must be under Discord limits (8MB for most servers)
   - Ensure image isn't corrupted

2. **Verify OpenAI Image Access**
   - Check if your OpenAI plan includes image model access
   - Some models require higher tier plans
   - Try checking model spelling: `/draw prompt model:gpt-image-1.5`

3. **Check Image URL Access**
   - Bot needs internet access to fetch Discord image URLs
   - Firewall might be blocking image downloads

### Memory and Performance Issues

#### Symptoms
- Bot becomes slow or unresponsive
- High memory usage
- Commands timeout frequently

#### Solutions

1. **Monitor Resource Usage**
   ```bash
   # Check memory usage
   ps aux | grep python | grep main.py
   
   # Check system resources
   htop
   free -h
   ```

2. **Clear Conversation History**
   - Use `/reset` in channels with long conversations
   - Restart bot to clear all in-memory state
   - Consider implementing conversation history limits

3. **Optimize Configuration**
   ```env
   # Reduce context size for mentions
   INCLUDE_NUM_CHATLINES=50

   # Use more efficient model
   DEFAULT_MODEL=gpt-5-nano
   ```

### Configuration Issues

#### Symptoms
- Bot starts but behaves unexpectedly
- Environment variables not loading
- Default settings not working

#### Solutions

1. **Verify .env File**
   ```bash
   # Check file exists and is readable
   ls -la .env
   cat .env
   
   # Check for syntax errors
   grep -v '^#' .env | grep -v '^$'
   ```

2. **Test Configuration Loading**
   ```bash
   # Test config import
   python -c "from src.config import *; print('Config loaded')"
   
   # Check specific variables
   python -c "import os; print(os.getenv('DISCORD_BOT_TOKEN', 'NOT_SET')[:10])"
   ```

3. **Common Configuration Mistakes**
   - Missing quotes around values with spaces
   - Extra spaces before/after variable names
   - Wrong variable names (case sensitive)
   - Comments on same line as variables

### Queue and Concurrency Issues

#### Symptoms
- Commands processed out of order
- "Queue is full" messages
- Bot seems to ignore some commands

#### Solutions

1. **Check Queue Status**
   - Look for "queue is full" messages in logs
   - Monitor bot logs for queue-related errors

2. **Reduce Concurrent Load**
   - Limit number of users using bot simultaneously
   - Use `/reset` to clear stuck conversations
   - Restart bot to clear queue

3. **Optimize Queue Settings**
   - Increase queue size in code if needed
   - Reduce timeout values for faster processing

### Network and Connectivity Issues

#### Symptoms
- Intermittent connection failures
- "Network error" messages
- Bot goes offline randomly

#### Solutions

1. **Check Internet Connection**
   ```bash
   # Test Discord connectivity
   ping discord.com
   
   # Test OpenAI connectivity
   ping api.openai.com
   ```

2. **Verify Firewall Settings**
   - Allow outbound HTTPS (port 443)
   - Allow Discord gateway connections
   - Check corporate firewall restrictions

3. **DNS Issues**
   ```bash
   # Test DNS resolution
   nslookup discord.com
   nslookup api.openai.com
   ```

## Debugging Tools

### Log Analysis

1. **Check Bot Logs**
   ```bash
   # View recent logs
   tail -f bot.log
   
   # Search for errors
   grep -i error bot.log
   
   # Check specific timeframe
   grep "2024-01-15 14:" bot.log
   ```

2. **System Logs**
   ```bash
   # Systemd logs
   sudo journalctl -u discord-bot -f
   
   # System messages
   sudo tail -f /var/log/syslog
   ```

### Testing Commands

1. **Basic Functionality Test**
   ```
   /chat Hello, are you working?
   /model
   /systemprompt view
   ```

2. **Image Functionality Test**
   ```
   /draw A simple red circle
   /chat Describe this image [attach small test image]
   ```

3. **Mention Test**
   ```
   @BotName can you see this message?
   ```

### Environment Debugging

```bash
# Check Python environment
python --version
pip list | grep discord
pip list | grep openai

# Check environment variables
env | grep DISCORD
env | grep OPENAI

# Test imports
python -c "import discord; print('Discord.py version:', discord.__version__)"
python -c "import openai; print('OpenAI version:', openai.__version__)"
```

## Error Message Reference

### Discord Errors

- **"Missing Permissions"**: Bot lacks required Discord permissions
- **"Unknown Interaction"**: Command took too long to respond (>3 seconds)
- **"Invalid Form Body"**: Malformed command parameters
- **"Rate Limited"**: Too many requests to Discord API

### OpenAI Errors

- **"Invalid API Key"**: Wrong or expired OpenAI API key
- **"Insufficient Quota"**: No credits or exceeded usage limits
- **"Model Not Found"**: Requested model doesn't exist or isn't accessible
- **"Content Policy Violation"**: Request violates OpenAI usage policies

### Bot-Specific Errors

- **"Queue is full"**: Too many concurrent commands
- **"Command timeout"**: Operation took too long to complete
- **"Configuration error"**: Missing or invalid environment variables
- **"Service unavailable"**: External service (OpenAI/Discord) is down

## Getting Help

### Before Asking for Help

1. **Check this troubleshooting guide**
2. **Review recent logs** for error messages
3. **Test with simple commands** first
4. **Verify configuration** is correct
5. **Try restarting** the bot

### Information to Provide

When reporting issues, include:

- **Error message** (exact text)
- **Command used** that caused the issue
- **Time** when issue occurred
- **Bot logs** around the time of the issue
- **Configuration** (without sensitive tokens)
- **Environment** (OS, Python version, etc.)

### Support Channels

- **GitHub Issues**: https://github.com/jobeus/cmwgpt/issues
- **Documentation**: Check other files in `docs/` folder
- **Discord API Documentation**: https://discord.com/developers/docs
- **OpenAI API Documentation**: https://platform.openai.com/docs

### Self-Help Resources

- **[Configuration Guide](configuration.md)**: Detailed setup instructions
- **[Commands Reference](commands.md)**: Complete command documentation
- **[Architecture Guide](architecture.md)**: Understanding how the bot works
- **[Development Guide](development.md)**: Setting up development environment

## Prevention Tips

### Regular Maintenance

1. **Monitor Logs Regularly**
   ```bash
   # Set up log rotation
   sudo logrotate -f /etc/logrotate.d/discord-bot
   ```

2. **Update Dependencies**
   ```bash
   # Check for updates
   pip list --outdated
   
   # Update safely
   pip install --upgrade discord.py openai
   ```

3. **Monitor Resource Usage**
   ```bash
   # Set up monitoring
   htop
   df -h
   ```

### Best Practices

- **Test changes** in development environment first
- **Keep backups** of working configurations
- **Monitor API usage** and costs
- **Set up alerts** for service failures
- **Document any custom changes**

### Security Maintenance

- **Rotate API keys** regularly
- **Monitor for unauthorized usage**
- **Keep bot permissions minimal**
- **Update dependencies** for security patches
- **Review logs** for suspicious activity
