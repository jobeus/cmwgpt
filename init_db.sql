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
    curl_command LONGTEXT GENERATED ALWAYS AS (
        CONCAT('curl -X ', method, ' ''', endpoint_url, ''' ',
        COALESCE(
            (SELECT GROUP_CONCAT(CONCAT('-H ''', k, ': ', JSON_UNQUOTE(v), '''') SEPARATOR ' ')
             FROM JSON_TABLE(JSON_KEYS(request_headers), '$[*]' COLUMNS(k VARCHAR(255) PATH '$')) as keys_seq
             JOIN JSON_TABLE(request_headers, '$' COLUMNS(v JSON PATH '$.*')) as vals ON 1=1), ''
        ),
        IF(request_body IS NOT NULL AND length(request_body) > 0, CONCAT(' -d ''', request_body, ''''), '')
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

