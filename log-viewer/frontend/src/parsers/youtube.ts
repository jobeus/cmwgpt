import type { ServiceMessage } from './types';

export function parseYoutube(requestBody: any, responseBody: any): ServiceMessage[] {
    const messages: ServiceMessage[] = [];

    const representsPython = typeof requestBody === 'string' && requestBody.includes('import');
    const reqContent = representsPython ? requestBody : `[Action: Fetching YouTube Transcript for video ${requestBody?.video_id || '(unknown video id)'}]`;
    messages.push({ role: 'system', content: reqContent });
    messages.push({ role: 'assistant', content: typeof responseBody === 'string' ? responseBody : JSON.stringify(responseBody, null, 2) });

    return messages;
}
