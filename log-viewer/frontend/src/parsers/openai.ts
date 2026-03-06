import type { ServiceMessage } from './types';

export function parseOpenAI(requestBody: any, responseBody: any): ServiceMessage[] {
    const messages: ServiceMessage[] = [];

    // Extract from request messages array
    if (requestBody?.messages && Array.isArray(requestBody.messages)) {
        requestBody.messages.forEach((m: any) => {
            messages.push({ role: m.role, content: m.content });
        });
    }
    // Extract from response choices
    if (responseBody?.choices && responseBody.choices[0]?.message) {
        messages.push({ role: 'assistant', content: responseBody.choices[0].message.content });
    }

    return messages;
}
