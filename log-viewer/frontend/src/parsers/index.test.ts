import { describe, expect, it } from 'vitest';
import { parseServiceMessages } from './index';

describe('parseServiceMessages', () => {
    it('dispatches OpenAI-style messages', () => {
        const messages = parseServiceMessages(
            'openai/chat/completions',
            { messages: [{ role: 'user', content: 'hello' }] },
            { choices: [{ message: { content: 'hi there' } }] }
        );

        expect(messages).toEqual([
            { role: 'user', content: 'hello' },
            { role: 'assistant', content: 'hi there' }
        ]);
    });

    it('dispatches Runpod image requests', () => {
        const messages = parseServiceMessages(
            'runpod/image',
            { input: { prompt: 'draw a cat', images: ['https://example.com/cat.png'] } },
            { output: { image_url: 'https://example.com/result.png' } }
        );

        expect(messages[0].role).toBe('user (image gen)');
        expect(messages[1]).toEqual({
            role: 'assistant',
            content: [{ type: 'image_url', image_url: { url: 'https://example.com/result.png' } }]
        });
    });

    it('formats unknown services as request/response fallbacks', () => {
        const messages = parseServiceMessages('custom/service', { hello: 'world' }, 'raw response');

        expect(messages).toEqual([
            { role: 'request', content: JSON.stringify({ hello: 'world' }, null, 2) },
            { role: 'response', content: 'raw response' }
        ]);
    });

    it('renders structured downloader pipeline steps as input/output cards', () => {
        const messages = parseServiceMessages(
            'downloader/tiktok/transcript',
            {
                format: 'pipeline_step.v1',
                title: 'TikTok audio → transcript',
                step: 'tiktok_transcript',
                summary: 'Prepared TikTok audio for transcription',
                data: { source_url: 'https://vt.tiktok.com/abc' }
            },
            {
                format: 'pipeline_step.v1',
                title: 'TikTok audio → transcript',
                step: 'tiktok_transcript',
                summary: 'Produced transcript text for TikTok content',
                data: { transcript_text: 'hello world' }
            }
        );

        expect(messages).toEqual([
            {
                role: 'request',
                content: {
                    type: 'pipeline_step',
                    side: 'input',
                    payload: expect.objectContaining({ title: 'TikTok audio → transcript' })
                }
            },
            {
                role: 'response',
                content: {
                    type: 'pipeline_step',
                    side: 'output',
                    payload: expect.objectContaining({ data: { transcript_text: 'hello world' } })
                }
            }
        ]);
    });
});