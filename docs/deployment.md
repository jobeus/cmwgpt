# 🚀 Deployment Guide

## Production Deployment

### Environment Setup

1. **Server Requirements**
   - Python 3.9 or later
   - 512MB RAM minimum (1GB recommended)
   - Stable internet connection
   - SSL/TLS support for HTTPS

2. **Clone and Setup**
   ```bash
   # Clone repository
   git clone https://github.com/jobeus/cmwgpt.git
   cd cmwgpt

   # Create production environment
   python3 -m venv venv
   source venv/bin/activate

   # Install dependencies
   pip install -r requirements.txt
   ```

3. **Configuration**
   ```bash
   # Copy and configure environment
   cp env.example .env
   # Edit .env with your production tokens
   ```

### Process Management

#### Using systemd (Linux)

1. **Create service file** `/etc/systemd/system/discord-bot.service`:
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

2. **Enable and start service**:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable discord-bot
   sudo systemctl start discord-bot
   sudo systemctl status discord-bot
   ```

#### Using PM2 (Node.js Process Manager)

```bash
# Install PM2
npm install -g pm2

# Create ecosystem file
cat > ecosystem.config.js << EOF
module.exports = {
  apps: [{
    name: 'discord-bot',
    script: 'python',
    args: 'main.py',
    cwd: '/path/to/cmwgpt',
    interpreter: '/path/to/cmwgpt/venv/bin/python',
    restart_delay: 10000,
    max_restarts: 10,
    env: {
      NODE_ENV: 'production'
    }
  }]
}
EOF

# Start with PM2
pm2 start ecosystem.config.js
pm2 save
pm2 startup
```

#### Using Supervisor

1. **Install supervisor**:
   ```bash
   sudo apt-get install supervisor
   ```

2. **Create config** `/etc/supervisor/conf.d/discord-bot.conf`:
   ```ini
   [program:discord-bot]
   command=/path/to/cmwgpt/venv/bin/python main.py
   directory=/path/to/cmwgpt
   user=your-user
   autostart=true
   autorestart=true
   redirect_stderr=true
   stdout_logfile=/var/log/discord-bot.log
   ```

3. **Start service**:
   ```bash
   sudo supervisorctl reread
   sudo supervisorctl update
   sudo supervisorctl start discord-bot
   ```

## Docker Deployment

### Development stack with Docker Compose

```bash
# Create your local Docker env file
cp .env.development.example .env.development

# Fill in .env.development, then start everything
docker compose --env-file .env.development up --build
```

This compose stack starts:
- the Discord bot
- the log-viewer backend
- the log-viewer frontend
- a MariaDB instance initialized from `init_db.sql`

### Helpful endpoints

- Frontend: http://localhost:5173
- Backend API: http://localhost:3001/api
- MariaDB: localhost:3306 (or whatever you set in DB_EXPOSE_PORT)

### Log viewer authentication in Docker development

When `LOG_VIEWER_DEV_AUTH_ENABLED=true`, the log-viewer backend uses the credentials below instead of PAM:

```env
LOG_VIEWER_DEV_USERNAME=devadmin
LOG_VIEWER_DEV_PASSWORD=change-me
```

Disable that mode outside Docker development if you want the backend to keep using PAM.

### Stopping the stack

```bash
docker compose --env-file .env.development down
```

### Using GitHub Container Registry

```bash
# Pull pre-built image
docker pull ghcr.io/jobeus/cmwgpt:latest

# Run pre-built image
docker run -d \
  --name discord-bot \
  --restart unless-stopped \
  --env-file .env \
  ghcr.io/jobeus/cmwgpt:latest
```

## Cloud Deployment

### Heroku

1. **Create Heroku app**:
   ```bash
   heroku create your-bot-name
   ```

2. **Set environment variables**:
   ```bash
   heroku config:set DISCORD_BOT_TOKEN=your_token
   heroku config:set OPENROUTER_API_KEY=your_key
   ```

3. **Deploy**:
   ```bash
   git push heroku main
   ```

4. **Scale worker**:
   ```bash
   heroku ps:scale worker=1
   ```

### Railway

1. **Connect GitHub repository** to Railway
2. **Set environment variables** in Railway dashboard
3. **Deploy automatically** on git push

### DigitalOcean App Platform

1. **Create app** from GitHub repository
2. **Configure environment variables**
3. **Set build and run commands**:
   - Build: `pip install -r requirements.txt`
   - Run: `python main.py`

## CI/CD Pipelines

### GitHub Actions Workflows

The project includes comprehensive CI/CD pipelines:

#### Continuous Integration (`.github/workflows/ci.yml`)
- **Triggers**: Push to main/master, Pull Requests
- **Python Versions**: 3.9, 3.10, 3.11, 3.12
- **Checks**: Linting, Testing, Coverage
- **Features**:
  - Dependency caching for faster builds
  - Multi-version Python testing
  - Code coverage reporting
  - Automatic formatting validation

#### Pull Request Checks (`.github/workflows/pr-checks.yml`)
- **Advanced PR Validation**: Title/description checks
- **Security Scanning**: Bandit for code security, Safety for dependencies
- **Targeted Testing**: Only tests files changed in PR
- **Size Analysis**: Warns about large PRs
- **Commit Message Validation**: Ensures meaningful commit messages

#### Release Automation (`.github/workflows/release.yml`)
- **Automatic Releases**: Triggered by version tags
- **Changelog Generation**: Auto-generated from git commits
- **Docker Images**: Builds and publishes to GitHub Container Registry
- **Security Validation**: Pre-release security scanning

### Setting Up CI/CD

1. **GitHub Actions** (included in repository)
   - Workflows run automatically on push/PR
   - No additional setup required

2. **Custom CI/CD**
   ```bash
   # Example deployment script
   #!/bin/bash
   set -e
   
   echo "Pulling latest changes..."
   git pull origin main
   
   echo "Installing dependencies..."
   pip install -r requirements.txt
   
   echo "Running tests..."
   make test
   
   echo "Restarting service..."
   sudo systemctl restart discord-bot
   
   echo "Deployment complete!"
   ```

## Monitoring and Logging

### Application Logging

The bot includes comprehensive logging:

```python
# Logging configuration in main.py
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s: %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
```

### Log Management

1. **Log Rotation**:
   ```bash
   # Install logrotate configuration
   sudo cat > /etc/logrotate.d/discord-bot << EOF
   /path/to/cmwgpt/bot.log {
       daily
       rotate 7
       compress
       delaycompress
       missingok
       notifempty
       create 644 your-user your-group
   }
   EOF
   ```

2. **Centralized Logging** (optional):
   - ELK Stack (Elasticsearch, Logstash, Kibana)
   - Fluentd
   - Syslog

### Health Monitoring

1. **Basic Health Check**:
   ```bash
   # Check if bot process is running
   ps aux | grep python | grep main.py
   
   # Check systemd service status
   sudo systemctl status discord-bot
   ```

2. **Advanced Monitoring**:
   - Prometheus + Grafana
   - New Relic
   - DataDog
   - Custom health check endpoints

## Security Considerations

### Environment Security

1. **Secure Token Storage**:
   - Use environment variables
   - Never commit tokens to git
   - Rotate tokens regularly

2. **File Permissions**:
   ```bash
   # Secure .env file
   chmod 600 .env
   
   # Secure application directory
   chmod -R 755 /path/to/cmwgpt
   ```

3. **User Isolation**:
   ```bash
   # Create dedicated user
   sudo useradd -r -s /bin/false discord-bot
   sudo chown -R discord-bot:discord-bot /path/to/cmwgpt
   ```

### Network Security

1. **Firewall Configuration**:
   ```bash
   # Allow only necessary ports
   sudo ufw allow ssh
   sudo ufw allow https
   sudo ufw enable
   ```

2. **SSL/TLS**:
   - Use HTTPS for all external communications
   - Keep certificates up to date

### Application Security

1. **Dependency Management**:
   ```bash
   # Check for security vulnerabilities
   pip audit
   
   # Update dependencies regularly
   pip install --upgrade -r requirements.txt
   ```

2. **Input Validation**:
   - Validate all user inputs
   - Sanitize data before processing
   - Implement rate limiting

## Backup and Recovery

### Data Backup

Currently, the bot uses in-memory storage. For production:

1. **Configuration Backup**:
   ```bash
   # Backup configuration
   cp .env .env.backup
   tar -czf config-backup-$(date +%Y%m%d).tar.gz .env
   ```

2. **Code Backup**:
   ```bash
   # Git-based backup
   git push origin main
   
   # Archive backup
   tar -czf bot-backup-$(date +%Y%m%d).tar.gz --exclude=venv .
   ```

### Disaster Recovery

1. **Recovery Plan**:
   - Document all configuration steps
   - Maintain updated deployment scripts
   - Test recovery procedures regularly

2. **Quick Recovery**:
   ```bash
   # Automated recovery script
   #!/bin/bash
   git clone https://github.com/jobeus/cmwgpt.git
   cd cmwgpt
   cp /backup/.env .
   make dev-setup
   sudo systemctl start discord-bot
   ```

## Performance Optimization

### Resource Optimization

1. **Memory Management**:
   - Monitor memory usage
   - Implement conversation history limits
   - Use efficient data structures

2. **CPU Optimization**:
   - Async/await for I/O operations
   - Efficient queue processing
   - Minimize blocking operations

### Scaling Considerations

1. **Horizontal Scaling**:
   - Multiple bot instances
   - Load balancing
   - Shared state management

2. **Vertical Scaling**:
   - Increase server resources
   - Optimize application performance
   - Database optimization (if implemented)

## Troubleshooting Deployment

### Common Issues

1. **Permission Errors**:
   ```bash
   # Fix file permissions
   chmod +x main.py
   chown -R user:group /path/to/cmwgpt
   ```

2. **Port Conflicts**:
   ```bash
   # Check port usage
   netstat -tulpn | grep :PORT
   ```

3. **Environment Issues**:
   ```bash
   # Verify environment
   python --version
   pip list
   env | grep DISCORD
   ```

For more troubleshooting help, see [troubleshooting.md](troubleshooting.md).
