import dotenv from 'dotenv';
import path from 'path';

dotenv.config({ path: path.join(__dirname, '../../../.env') });

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
export const JWT_EXPIRES_IN = process.env.JWT_EXPIRES_IN?.trim() || (isProduction ? '12h' : '7d');