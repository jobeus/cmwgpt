-- MariaDB Initialization Script for CMWGPT

CREATE DATABASE IF NOT EXISTS cmwgpt CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE cmwgpt;

CREATE TABLE IF NOT EXISTS api_request_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    service_name VARCHAR(100) NOT NULL COMMENT 'e.g., openai, runpod, wikipedia, twitter',
    method VARCHAR(10) NOT NULL COMMENT 'GET, POST, etc.',
    endpoint_url TEXT NOT NULL,
    request_headers JSON,
    request_body LONGTEXT,
    
    -- Virtual column to regenerate exactly what was sent via cURL
    -- We use nested REGEXP_REPLACE to elegantly map JSON keys to -H flags bypassing MariaDB subquery limitations
    -- We also natively escape JSON single quotes to `'\''` to prevent bash injection or zsh event expansion errors
    curl_command LONGTEXT GENERATED ALWAYS AS (
        CONCAT('curl -X ', method, ' ''', REPLACE(endpoint_url, '''', CONCAT('''', '\\', '''', '''')), ''' ',
        IF(request_headers IS NULL OR JSON_TYPE(request_headers) != 'OBJECT' OR JSON_LENGTH(request_headers) = 0, '',
            REGEXP_REPLACE(
                REGEXP_REPLACE(
                    REGEXP_REPLACE(
                        REPLACE(request_headers, '''', CONCAT('''', '\\', '''', '''')),
                        '"\\s*:\\s*"', ': '
                    ),
                    '"\\s*,\\s*"', ''' -H '''
                ),
                '^\\{\\s*"(.*)"\\s*\\}$',
                '-H ''\\1'''
            )
        ),
        IF(request_body IS NOT NULL AND length(request_body) > 0, 
           CONCAT(' -d ''', REPLACE(request_body, '''', CONCAT('''', '\\', '''', '''')), ''''), 
           ''
        )
        )
    ) VIRTUAL,
    
    response_status INT,
    response_headers JSON,
    response_body LONGTEXT COMMENT 'JSON alias / validation via checks where applicable',
    cost DECIMAL(12, 8) DEFAULT 0.0,
    discord_user_id BIGINT UNSIGNED NULL,
    discord_channel_id BIGINT UNSIGNED NULL,
    
    INDEX idx_timestamp (timestamp),
    INDEX idx_service_name (service_name),
    INDEX idx_discord_user_id (discord_user_id),
    INDEX idx_discord_channel_id (discord_channel_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Per-user memory: what the bot has learned about each member, per server.
-- Safe to re-run against an existing database (every statement is IF NOT EXISTS).
CREATE TABLE IF NOT EXISTS user_memory (
    guild_id BIGINT UNSIGNED NOT NULL,
    user_id BIGINT UNSIGNED NOT NULL,
    profile TEXT NOT NULL DEFAULT '' COMMENT 'Bot-learned notes; rewritten by the nightly merge',
    self_notes TEXT NOT NULL DEFAULT '' COMMENT 'Added by the user via /memory add; never rewritten by the merge',
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS user_memory_history (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    guild_id BIGINT UNSIGNED NOT NULL,
    user_id BIGINT UNSIGNED NOT NULL,
    source VARCHAR(20) NOT NULL COMMENT 'nightly, fill, manual, clear',
    profile TEXT NOT NULL,
    self_notes TEXT NOT NULL,
    observations TEXT NULL COMMENT 'Facts extracted from the window, before merge',
    window_start DATETIME NULL COMMENT 'UTC',
    window_end DATETIME NULL COMMENT 'UTC',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user_created (guild_id, user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS user_memory_job_state (
    guild_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    processed_through DATETIME NOT NULL COMMENT 'UTC; messages before this are already in profiles',
    last_run_at DATETIME NULL,
    last_status VARCHAR(255) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
