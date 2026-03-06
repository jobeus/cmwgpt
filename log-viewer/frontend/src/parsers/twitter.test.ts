import { describe, expect, it } from 'vitest';
import { parseTwitter } from './twitter';

describe('parseTwitter', () => {
    it('builds a tweet card and helper snippet for direct tweet payloads', () => {
        const messages = parseTwitter(
            null,
            { data: [{ text: 'Hello from X' }] },
            'https://x-com2.p.rapidapi.com/v2/TweetDetail/?id=12345'
        );

        expect(messages[0].role).toBe('system');
        expect(String(messages[0].content)).toContain('tweet_id = "12345"');
        expect(String(messages[0].content)).toContain('https://x.com/i/status/12345');
        expect(messages[1]).toMatchObject({
            role: 'assistant',
            content: {
                type: 'tweet',
                author: 'Twitter User',
                text: 'Hello from X'
            }
        });
    });
});