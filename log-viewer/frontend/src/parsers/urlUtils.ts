import type { ServiceMessage } from './types';

export function parseUrlUtils(requestBody: any, responseBody: any): ServiceMessage[] {
    const messages: ServiceMessage[] = [];

    const representsPython = typeof requestBody === 'string' && requestBody.includes('import');
    const reqContent = representsPython ? requestBody : `[Action: Scraping Article]`;
    messages.push({ role: 'system', content: reqContent });
    messages.push({ role: 'assistant', content: typeof responseBody === 'string' ? responseBody : JSON.stringify(responseBody, null, 2) });

    return messages;
}
