import { describe, expect, it } from 'vitest';
import { proxyMediaUrl } from './media';

describe('proxyMediaUrl', () => {
    it('strips query strings by default', () => {
        expect(proxyMediaUrl('https://cdn.example.com/file.mp4?token=secret')).toBe(
            '/api/proxy-media?url=https%3A%2F%2Fcdn.example.com%2Ffile.mp4'
        );
    });

    it('preserves query strings when requested', () => {
        expect(proxyMediaUrl('https://cdn.example.com/file.mp4?token=secret', false)).toBe(
            '/api/proxy-media?url=https%3A%2F%2Fcdn.example.com%2Ffile.mp4%3Ftoken%3Dsecret'
        );
    });

    it('falls back to string splitting for malformed URLs', () => {
        expect(proxyMediaUrl('not-a-valid-url?token=secret')).toBe(
            '/api/proxy-media?url=not-a-valid-url'
        );
    });
});