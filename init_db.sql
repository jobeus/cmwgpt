-- MariaDB Initialization Script for CMWGPT
-- (the old api_request_logs table is no longer used: DROP TABLE IF EXISTS api_request_logs;)

CREATE DATABASE IF NOT EXISTS cmwgpt CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE cmwgpt;

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
    source VARCHAR(20) NOT NULL COMMENT 'nightly, fill, manual, correct, clear',
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
