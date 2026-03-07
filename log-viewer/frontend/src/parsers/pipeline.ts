import type { ServiceMessage } from './types';

export function parsePipelineStep(requestBody: any, responseBody: any): ServiceMessage[] {
    const messages: ServiceMessage[] = [];

    if (requestBody?.format === 'pipeline_step.v1') {
        messages.push({
            role: 'request',
            content: {
                type: 'pipeline_step',
                side: 'input',
                payload: requestBody,
            },
        });
    }

    if (responseBody?.format === 'pipeline_step.v1') {
        messages.push({
            role: 'response',
            content: {
                type: 'pipeline_step',
                side: 'output',
                payload: responseBody,
            },
        });
    }

    return messages;
}