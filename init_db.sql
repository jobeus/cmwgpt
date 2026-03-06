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
    cost DECIMAL(10, 6) DEFAULT 0.0,
    discord_user_id BIGINT UNSIGNED NULL,
    discord_channel_id BIGINT UNSIGNED NULL,
    
    INDEX idx_timestamp (timestamp),
    INDEX idx_service_name (service_name),
    INDEX idx_discord_user_id (discord_user_id),
    INDEX idx_discord_channel_id (discord_channel_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
