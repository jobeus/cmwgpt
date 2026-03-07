import type { ServiceMessage } from './types';
import { parseOpenAI } from './openai';
import { parseRunpod } from './runpod';
import { parseYoutube } from './youtube';
import { parseGroq } from './groq';
import { parseTwitter } from './twitter';
import { parseUrlUtils } from './urlUtils';
import { parsePipelineStep } from './pipeline';

/**
 * Dispatch to the right service parser based on service name.
 * Returns an array of { role, content } messages for rendering.
 */
export function parseServiceMessages(
    serviceName: string,
    requestBody: any,
    responseBody: any,
    endpointUrl?: string | null
): ServiceMessage[] {
    if (requestBody?.format === 'pipeline_step.v1' || responseBody?.format === 'pipeline_step.v1') {
        return parsePipelineStep(requestBody, responseBody);
    }
    if (serviceName.startsWith('openai/') || serviceName.startsWith('anthropic/')) {
        return parseOpenAI(requestBody, responseBody);
    }
    if (serviceName.startsWith('runpod/')) {
        return parseRunpod(requestBody, responseBody);
    }
    if (serviceName.startsWith('youtube/')) {
        return parseYoutube(requestBody, responseBody);
    }
    if (serviceName.startsWith('groq/whisper')) {
        return parseGroq(requestBody, responseBody);
    }
    if (serviceName.startsWith('rapidapi/twitter')) {
        return parseTwitter(requestBody, responseBody, endpointUrl);
    }
    if (serviceName.startsWith('url_utils/')) {
        return parseUrlUtils(requestBody, responseBody);
    }

    // Fallback for unknown services
    const messages: ServiceMessage[] = [];
    if (requestBody) messages.push({ role: 'request', content: typeof requestBody === 'object' ? JSON.stringify(requestBody, null, 2) : requestBody });
    if (responseBody) messages.push({ role: 'response', content: typeof responseBody === 'object' ? JSON.stringify(responseBody, null, 2) : responseBody });
    return messages;
}
