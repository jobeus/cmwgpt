export interface PipelinePayload {
    format: string;
    title?: string;
    step?: string;
    summary?: string;
    data?: any;
    artifacts?: Array<Record<string, any>>;
    replay?: Record<string, string>;
    meta?: Record<string, any>;
}

export const isPipelinePayload = (value: any): value is PipelinePayload => {
    return Boolean(value && typeof value === 'object' && value.format === 'pipeline_step.v1');
};

const stringifyValue = (value: any): string => {
    if (value == null) return '';
    if (typeof value === 'string') return value;
    if (typeof value === 'number' || typeof value === 'boolean') return String(value);
    return JSON.stringify(value);
};

export const getPipelineReplay = (payload: any): { label: string; text: string } | null => {
    if (!isPipelinePayload(payload) || !payload.replay) return null;
    if (payload.replay.python) return { label: 'Python replay', text: payload.replay.python };
    if (payload.replay.shell) return { label: 'Shell replay', text: payload.replay.shell };
    if (payload.replay.curl) return { label: 'cURL replay', text: payload.replay.curl };
    return null;
};

export const getPipelineTitle = (requestBody: any, responseBody: any, fallback: string): string => {
    if (isPipelinePayload(requestBody) && requestBody.title) return requestBody.title;
    if (isPipelinePayload(responseBody) && responseBody.title) return responseBody.title;
    return fallback;
};

export const getPipelineSnippet = (payload: any, fallback = ''): string => {
    if (!isPipelinePayload(payload)) return fallback;
    if (payload.summary) return payload.summary;

    const data = payload.data;
    if (!data) return payload.title || fallback;

    const candidates = [
        data.transcript_text,
        data.context,
        data.text,
        data.html,
        data.message_text,
        data.url,
        data.source_url,
        data.video_id,
    ];

    for (const candidate of candidates) {
        const text = stringifyValue(candidate).trim();
        if (text) return text.length > 180 ? `${text.slice(0, 180)}...` : text;
    }

    if (Array.isArray(data)) return `[${data.length} items]`;
    if (typeof data === 'object') {
        const keys = Object.keys(data);
        return keys.length > 0 ? `Fields: ${keys.slice(0, 5).join(', ')}` : (payload.title || fallback);
    }

    return stringifyValue(data) || payload.title || fallback;
};