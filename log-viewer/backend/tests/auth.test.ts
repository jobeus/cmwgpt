import { describe, expect, it, vi } from 'vitest';

vi.mock('authenticate-pam', () => ({
    default: {
        authenticate: vi.fn()
    }
}));

import {
    hasDevelopmentAuthCredentials,
    isDevelopmentAuthEnabled,
    matchesDevelopmentCredentials
} from '../src/auth';

describe('log viewer development auth helpers', () => {
    it('detects whether development auth is enabled', () => {
        expect(isDevelopmentAuthEnabled({ LOG_VIEWER_DEV_AUTH_ENABLED: 'true' })).toBe(true);
        expect(isDevelopmentAuthEnabled({ LOG_VIEWER_DEV_AUTH_ENABLED: 'TRUE' })).toBe(true);
        expect(isDevelopmentAuthEnabled({ LOG_VIEWER_DEV_AUTH_ENABLED: 'false' })).toBe(false);
        expect(isDevelopmentAuthEnabled({})).toBe(false);
    });

    it('requires both development auth credentials', () => {
        expect(hasDevelopmentAuthCredentials({
            LOG_VIEWER_DEV_USERNAME: 'devadmin',
            LOG_VIEWER_DEV_PASSWORD: 'change-me'
        })).toBe(true);

        expect(hasDevelopmentAuthCredentials({ LOG_VIEWER_DEV_USERNAME: 'devadmin' })).toBe(false);
        expect(hasDevelopmentAuthCredentials({ LOG_VIEWER_DEV_PASSWORD: 'change-me' })).toBe(false);
    });

    it('matches only the configured development credentials', () => {
        const env = {
            LOG_VIEWER_DEV_AUTH_ENABLED: 'true',
            LOG_VIEWER_DEV_USERNAME: 'devadmin',
            LOG_VIEWER_DEV_PASSWORD: 'change-me'
        };

        expect(matchesDevelopmentCredentials('devadmin', 'change-me', env)).toBe(true);
        expect(matchesDevelopmentCredentials('devadmin', 'wrong-password', env)).toBe(false);
        expect(matchesDevelopmentCredentials('someone-else', 'change-me', env)).toBe(false);
        expect(matchesDevelopmentCredentials('devadmin', 'change-me', {
            ...env,
            LOG_VIEWER_DEV_AUTH_ENABLED: 'false'
        })).toBe(false);
    });
});