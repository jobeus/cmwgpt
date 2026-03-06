import type { ServiceMessage } from './types';

export function parseRunpod(requestBody: any, responseBody: any): ServiceMessage[] {
    const messages: ServiceMessage[] = [];

    const input = requestBody?.input || {};
    const prompt = input.prompt || '(No prompt provided)';

    const content: any[] = [{ type: 'text', text: prompt }];
    if (input.images && Array.isArray(input.images)) {
        input.images.forEach((img: string) => {
            content.push({ type: 'image_url', image_url: { url: img } });
        });
    }
    messages.push({ role: 'user (image gen)', content });

    // Response
    if (responseBody?.output?.image_url || responseBody?.output?.result) {
        messages.push({
            role: 'assistant',
            content: [{ type: 'image_url', image_url: { url: responseBody.output.image_url || responseBody.output.result } }]
        });
    }

    return messages;
}
