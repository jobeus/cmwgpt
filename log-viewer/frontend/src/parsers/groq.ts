import type { ServiceMessage } from './types';
import { extractAudioFromHex } from '../utils/media';

export function parseGroq(requestBody: any, responseBody: any): ServiceMessage[] {
    const messages: ServiceMessage[] = [];

    const content: any[] = [];

    if (typeof requestBody === 'string' && requestBody.length > 200) {
        const audioDataUri = extractAudioFromHex(requestBody);
        if (audioDataUri) {
            // If we successfully extracted the audio file from the raw hex, show the inline player!
            content.push({
                type: 'input_audio',
                input_audio: { data: audioDataUri, format: 'mp3', is_blob_uri: true }
            });
        } else {
            content.push({ type: 'text', text: `[Action: Transcribing Audio to Groq Api (Hex Decode Failed)]` });
        }
    } else {
        content.push({ type: 'text', text: `[Action: Transcribing Audio to Groq Api]` });
    }

    messages.push({ role: 'system', content });
    messages.push({ role: 'assistant', content: typeof responseBody === 'string' ? responseBody : JSON.stringify(responseBody, null, 2) });

    return messages;
}
