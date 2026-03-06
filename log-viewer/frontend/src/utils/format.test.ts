import { describe, expect, it } from 'vitest';
import { sanitizeJsonForRawView, truncateBase64 } from './format';

describe('format helpers', () => {
    it('truncates large data URIs', () => {
        const base64 = `data:image/png;base64,${'a'.repeat(250)}`;

        expect(truncateBase64(base64)).toBe('data:image/png;base64,[BASE64_DATA_TRUNCATED]');
    });

    it('recursively sanitizes nested JSON values', () => {
        const input = {
            nested: [`data:audio/mpeg;base64,${'b'.repeat(260)}`],
            plain: 'hello'
        };

        expect(sanitizeJsonForRawView(input)).toEqual({
            nested: ['data:audio/mpeg;base64,[BASE64_DATA_TRUNCATED]'],
            plain: 'hello'
        });
    });
});