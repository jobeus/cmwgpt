
import userMapData from '../user_map.json';
import { Image as ImageIcon, FileAudio, ExternalLink } from 'lucide-react';

const userMap = userMapData as Record<string, string>;
const GUILD_ID = import.meta.env.VITE_DISCORD_GUILD_ID || '1120463633693024346'; // fallback for demo

// Helper to truncate raw base64 strings so they don't break JSON views
export const truncateBase64 = (str: string) => {
    if (str.length > 200 && str.startsWith('data:')) {
        const parts = str.split(',');
        if (parts.length > 1) {
            return `${parts[0]},[BASE64_DATA_TRUNCATED]`;
        }
    }
    return str;
};

// Deep clone and truncate base64 in objects
export const sanitizeJsonForRawView = (obj: any): any => {
    if (typeof obj === 'string') return truncateBase64(obj);
    if (Array.isArray(obj)) return obj.map(sanitizeJsonForRawView);
    if (obj !== null && typeof obj === 'object') {
        const newObj: any = {};
        for (const key in obj) {
            newObj[key] = sanitizeJsonForRawView(obj[key]);
        }
        return newObj;
    }
    return obj;
};

interface ParsedMessage {
    timestamp?: string;
    msgId?: string;
    userId?: string;
    userName?: string;
    content: string;
}

const parseDiscordPrefix = (text: string): ParsedMessage => {
    // Regex matches: [2026-03-04 13:13:17] [1478742130095554613] <@392013989930074127>: message text
    const regex = /^\[(.*?)\] \[(\d+)\] <@(\d+)>:\s*([\s\S]*)/;
    const match = text.match(regex);
    if (match) {
        return {
            timestamp: match[1],
            msgId: match[2],
            userId: match[3],
            userName: userMap[match[3]] || match[3], // map to username if exists
            content: match[4]
        };
    }
    return { content: text };
};

const renderMessageContent = (content: any, channelId: string | null) => {
    if (typeof content === 'string') {
        const parsed = parseDiscordPrefix(content);
        return (
            <div className="text-gray-200 text-sm whitespace-pre-wrap font-sans">
                {parsed.msgId && (
                    <div className="flex items-center space-x-2 mb-1 text-xs text-gray-500">
                        <span className="font-semibold text-blue-400">{parsed.userName}</span>
                        <span>{parsed.timestamp}</span>
                        {channelId && (
                            <a
                                href={`discord://-/channels/${GUILD_ID}/${channelId}/${parsed.msgId}`}
                                target="_blank" rel="noreferrer"
                                className="hover:text-blue-400 flex items-center transition-colors"
                                title="View in Discord"
                            >
                                <ExternalLink className="w-3 h-3 ml-1" />
                            </a>
                        )}
                    </div>
                )}
                <div className="pl-1 border-l-2 border-gray-700 ml-1 mt-1 pt-1 opacity-90">
                    {parsed.content}
                </div>
            </div>
        );
    }

    // Handle OpenAI vision or array content
    if (Array.isArray(content)) {
        return (
            <div className="space-y-4">
                {content.map((part, idx) => {
                    if (part.type === 'text') {
                        return <div key={idx}>{renderMessageContent(part.text, channelId)}</div>;
                    }
                    if (part.type === 'image_url') {
                        const url = part.image_url.url;
                        return (
                            <div key={idx} className="mt-2 text-sm text-gray-400 flex flex-col items-start border border-gray-800 bg-gray-900 rounded-lg p-3 inline-block max-w-sm">
                                <div className="flex items-center mb-2">
                                    <ImageIcon className="w-4 h-4 mr-2 text-blue-400" />
                                    <span>Attached Image</span>
                                </div>
                                {url.startsWith('data:image') ? (
                                    <img src={url} alt="Attached" className="max-h-64 rounded-md border border-gray-700" />
                                ) : (
                                    <a href={url} target="_blank" rel="noreferrer" className="text-blue-500 hover:underline break-all">
                                        {url}
                                    </a>
                                )}
                            </div>
                        );
                    }
                    if (part.type === 'input_audio') {
                        // Handle base64 audio if provided
                        const rawData = part.input_audio.data;
                        const format = part.input_audio.format || 'mp3';
                        if (rawData) {
                            return (
                                <div key={idx} className="mt-2 text-sm text-gray-400 flex flex-col items-start border border-gray-800 bg-gray-900 rounded-lg p-3 inline-block max-w-sm">
                                    <div className="flex items-center mb-2">
                                        <FileAudio className="w-4 h-4 mr-2 text-amber-400" />
                                        <span>Attached Audio ({format})</span>
                                    </div>
                                    <audio controls className="w-full">
                                        <source src={`data:audio/${format};base64,${rawData}`} />
                                        Your browser does not support the audio element.
                                    </audio>
                                </div>
                            )
                        }
                    }
                    return <div key={idx} className="text-yellow-500 text-xs font-mono whitespace-pre-wrap">{JSON.stringify(part, null, 2)}</div>;
                })}
            </div>
        );
    }

    if (typeof content === 'object') {
        return <div className="text-gray-400 text-xs font-mono whitespace-pre-wrap break-all">{JSON.stringify(content, null, 2)}</div>;
    }

    return <div className="text-red-400 text-xs">Unparseable content</div>;
};

export const ConversationView = ({ requestBody, responseBody, channelId, serviceName }: { requestBody: any, responseBody: any, channelId: string | null, serviceName: string }) => {
    let messages: { role: string, content: any }[] = [];

    // Parse based on service name
    if (serviceName.startsWith('openai/') || serviceName.startsWith('anthropic/')) {
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
    } else if (serviceName.startsWith('runpod/')) {
        // Runpod Image Generation / Editing
        const input = requestBody?.input || {};
        const prompt = input.prompt || '(No prompt provided)';

        let content: any[] = [{ type: 'text', text: prompt }];
        if (input.images && Array.isArray(input.images)) {
            input.images.forEach((img: string) => {
                // If base64 is passed to pruna/seedream edit
                if (img.startsWith('data:')) {
                    content.push({ type: 'image_url', image_url: { url: img } });
                } else {
                    content.push({ type: 'image_url', image_url: { url: img } });
                }
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
    } else if (serviceName.startsWith('youtube/')) {
        // YouTube Transcripts
        const representsPython = typeof requestBody === 'string' && requestBody.includes('import');
        const reqContent = representsPython ? requestBody : `[Action: Fetching YouTube Transcript for video ${requestBody?.video_id || '(unknown video id)'}]`;
        messages.push({ role: 'system', content: reqContent });
        messages.push({ role: 'assistant', content: typeof responseBody === 'string' ? responseBody : JSON.stringify(responseBody, null, 2) });
    } else if (serviceName.startsWith('groq/whisper')) {
        // Groq Audio Transcriptions 
        let content: any[] = [{ type: 'text', text: `[Action: Transcribing Audio to Groq Api]` }];

        messages.push({ role: 'system', content });
        messages.push({ role: 'assistant', content: typeof responseBody === 'string' ? responseBody : JSON.stringify(responseBody, null, 2) });
    } else if (serviceName.startsWith('rapidapi/twitter')) {
        // Twitter RapidAPI Fetch
        messages.push({ role: 'system', content: `[Action: Fetching Twitter Data via RapidAPI]` });
        messages.push({ role: 'assistant', content: typeof responseBody === 'string' ? responseBody : JSON.stringify(responseBody, null, 2) });
    } else if (serviceName.startsWith('url_utils/')) {
        // Article scraping
        const representsPython = typeof requestBody === 'string' && requestBody.includes('import');
        const reqContent = representsPython ? requestBody : `[Action: Scraping Article]`;
        messages.push({ role: 'system', content: reqContent });
        messages.push({ role: 'assistant', content: typeof responseBody === 'string' ? responseBody : JSON.stringify(responseBody, null, 2) });
    } else {
        // Fallback for unknown services
        if (requestBody) messages.push({ role: 'request', content: typeof requestBody === 'object' ? JSON.stringify(requestBody, null, 2) : requestBody });
        if (responseBody) messages.push({ role: 'response', content: typeof responseBody === 'object' ? JSON.stringify(responseBody, null, 2) : responseBody });
    }

    if (messages.length === 0) {
        return (
            <div className="p-8 text-center text-gray-500 border border-dashed border-gray-800 rounded-xl my-4">
                Could not parse conversation from this request type. Try the Raw view.
            </div>
        );
    }

    return (
        <div className="space-y-6 my-4 w-full">
            {messages.map((msg, idx) => {
                const isUser = msg.role.includes('user') || msg.role.includes('request') || msg.role === 'system';
                return (
                    <div key={idx} className={`flex w-full ${isUser ? 'justify-start' : 'justify-end'}`}>
                        <div className={`max-w-[80%] rounded-2xl px-5 py-4 shadow-sm ${isUser ? 'bg-gray-800 text-gray-100 border border-gray-700/50' : 'bg-blue-900/30 border border-blue-500/20 text-gray-100'
                            }`}>
                            {msg.role && <div className="text-xs font-bold uppercase tracking-wider mb-2 opacity-50">{msg.role}</div>}
                            {renderMessageContent(msg.content, channelId)}
                        </div>
                    </div>
                );
            })}
        </div>
    );
};
