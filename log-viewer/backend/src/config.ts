import { loadResolvedEnvFile } from './env';

loadResolvedEnvFile();

const normalizeOrigin = (origin: string) => origin.trim().replace(/\/$/, '');

export const isProduction = process.env.NODE_ENV === 'production';

const configuredOrigins = (process.env.LOG_VIEWER_ALLOWED_ORIGINS || '')
    .split(',')
    .map(normalizeOrigin)
    .filter(Boolean);

const developmentOrigins = [
    'http://localhost:5173',
    'http://127.0.0.1:5173',
    'http://localhost:4173',
    'http://127.0.0.1:4173'
];

export const allowedOrigins = configuredOrigins.length > 0
    ? [...new Set(configuredOrigins)]
    : (isProduction ? [] : developmentOrigins);

if (isProduction && allowedOrigins.length === 0) {
    throw new Error('LOG_VIEWER_ALLOWED_ORIGINS must be set in production');
}

const configuredJwtSecret = process.env.JWT_SECRET?.trim();

if (!configuredJwtSecret && isProduction) {
    throw new Error('JWT_SECRET must be set in production');
}

if (!configuredJwtSecret) {
    console.warn('Using fallback JWT secret for non-production log-viewer development');
}

export const JWT_SECRET = configuredJwtSecret || 'fallback-secret-for-dev-only-change-in-prod';

// Accepts a plain number of seconds or a vercel/ms-style duration (e.g. "12h", "7 days"),
// matching what jsonwebtoken's expiresIn option understands. Fail fast at startup instead
// of throwing inside the login callback.
const JWT_EXPIRES_IN_PATTERN = /^\d+(\.\d+)?\s*(milliseconds?|msecs?|ms|seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|w|years?|yrs?|y)?$/i;

const configuredJwtExpiresIn = process.env.JWT_EXPIRES_IN?.trim();

if (configuredJwtExpiresIn && !JWT_EXPIRES_IN_PATTERN.test(configuredJwtExpiresIn)) {
    throw new Error(`Invalid JWT_EXPIRES_IN value "${configuredJwtExpiresIn}": use seconds (e.g. "3600") or a duration like "12h" or "7d"`);
}

export const JWT_EXPIRES_IN = configuredJwtExpiresIn || (isProduction ? '12h' : '7d');