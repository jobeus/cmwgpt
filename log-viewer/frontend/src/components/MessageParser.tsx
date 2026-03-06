import { useState } from 'react';
import userMapData from '../user_map.json';
import { Image as ImageIcon, FileAudio, ExternalLink, Copy, Check } from 'lucide-react';

const userMap = userMapData as Record<string, string>;
const GUILD_ID = import.meta.env.VITE_DISCORD_GUILD_ID || '1120463633693024346'; // fallback for demo

export const CopyButton = ({ text, className = '' }: { text: string, className?: string }) => {
    const [copied, setCopied] = useState(false);

    const handleCopy = async () => {
        try {
            await navigator.clipboard.writeText(text);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        } catch (err) {
            console.error('Failed to copy text: ', err);
        }
    };

    return (
        <button
            onClick={handleCopy}
            title="Copy text"
            className={`p-1.5 rounded-md text-gray-400 hover:text-gray-200 hover:bg-gray-700/50 transition-colors ${className}`}
        >
            {copied ? <Check className="w-4 h-4 text-emerald-400" /> : <Copy className="w-4 h-4" />}
        </button>
    );
};

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

// Helper to extract audio binary from a raw hex payload
export const extractAudioFromHex = (hexString: string): string | null => {
    try {
        if (!hexString || hexString.length < 100) return null;

        // Quick peek at the first few characters to see if it even looks like hex
        // We avoid heavy regex on 10MB strings because it crashes Safari/iOS and some V8 limits
        const head = hexString.substring(0, 100);
        if (!/^[0-9a-fA-F]+$/.test(head)) return null;

        // Convert hex to Uint8Array
        const bytes = new Uint8Array(hexString.length / 2);
        for (let i = 0; i < hexString.length; i += 2) {
            bytes[i / 2] = parseInt(hexString.substring(i, i + 2), 16);
        }

        // Check if there are multipart boundaries
        const decoder = new TextDecoder('latin1');
        const headStr = decoder.decode(bytes.subarray(0, 500));

        let audioBytes = bytes;

        // If it contains a filename boundary, extract just the binary part
        const fileContentIdx = headStr.indexOf('filename=');
        if (fileContentIdx !== -1) {
            const str = decoder.decode(bytes);
            const headerEndIdx = str.indexOf('\r\n\r\n', fileContentIdx);
            if (headerEndIdx !== -1) {
                const binaryStartIndex = headerEndIdx + 4;
                const nextBoundaryIdx = str.indexOf('\r\n--', binaryStartIndex);
                if (nextBoundaryIdx !== -1) {
                    audioBytes = bytes.slice(binaryStartIndex, nextBoundaryIdx);
                }
            }
        } else {
            // Otherwise, it's just a raw audio file (e.g. ID3 header '494433' or MPEG sync word 'fffb')
            // We can just dump the whole bytes array into the blob!
            audioBytes = bytes;
        }

        // Convert to a blob URI directly, avoiding `btoa()` memory crashes and slow UI string concatenation loops
        const blob = new Blob([audioBytes], { type: 'audio/mpeg' });
        return URL.createObjectURL(blob);
    } catch (e) {
        console.error("Failed to parse audio hex:", e);
        return null;
    }
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
                        return <div key={idx} className="whitespace-pre-wrap">{renderMessageContent(part.text, channelId)}</div>;
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
                    if (part.type === 'video') {
                        const url = part.video.url;
                        return (
                            <div key={idx} className="mt-2 text-sm text-gray-400 flex flex-col items-start border border-gray-800 bg-gray-900 rounded-lg p-3 inline-block max-w-[24rem]">
                                <div className="flex items-center mb-2">
                                    <ImageIcon className="w-4 h-4 mr-2 text-purple-400" />
                                    <span>Attached Video</span>
                                </div>
                                <video controls className="w-full rounded-md border border-gray-700 bg-black max-h-64" preload="metadata">
                                    <source src={url} type="video/mp4" />
                                    Your browser does not support the video tag.
                                </video>
                            </div>
                        );
                    }
                    if (part.type === 'input_audio') {
                        // Handle base64 or blob URL audio if provided
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
                                        <source src={part.input_audio.is_blob_uri ? rawData : `data:audio/${format};base64,${rawData}`} />
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
        let content: any[] = [];

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
    } else if (serviceName.startsWith('rapidapi/twitter')) {
        // Twitter RapidAPI Fetch
        const targetUrl = requestBody?.url || requestBody?.tweet_url || requestBody?.id || "Unknown URL";
        messages.push({ role: 'system', content: `[Action: Fetching Twitter Data via RapidAPI for ${targetUrl}]` });

        // Try to format the JSON data into a readable tweet + replies
        let contentArray: any[] = [];
        let hasMedia = false;

        if (typeof responseBody === 'object' && responseBody?.data) {
            try {
                // Determine if we are on the threaded_conversation v2 (which has instructions array) 
                // or standard direct v2 (with data + includes object)
                const isThreaded = responseBody.data.threaded_conversation_with_injections_v2 !== undefined;

                let mainText = "";
                let mainAuthor = "";
                let repliesText = "";

                const extractTweetText = (result: any) => {
                    const note = result?.note_tweet;
                    if (note && note.note_tweet_results?.result?.text) {
                        return note.note_tweet_results.result.text;
                    }
                    return result?.legacy?.full_text || result?.text || "";
                };

                const extractAuthor = (result: any) => {
                    return result?.core?.user_results?.result?.legacy?.name || "Twitter User";
                };

                // ---- Extract text and Replies ---- 
                if (isThreaded) {
                    const entries = responseBody.data.threaded_conversation_with_injections_v2.instructions[1].entries;
                    // Main tweet
                    const mainResult = entries[0].content.itemContent.tweet_results.result;
                    mainAuthor = extractAuthor(mainResult);
                    mainText = extractTweetText(mainResult);

                    // Replies
                    const replies = [];
                    for (let i = 1; i < Math.min(6, entries.length); i++) {
                        const entry = entries[i];
                        try {
                            let replyResult;
                            const items = entry.content.items;
                            if (items && items.length > 0) {
                                replyResult = items[0].item.itemContent.tweet_results.result;
                            } else {
                                replyResult = entry.content.itemContent.tweet_results.result;
                            }

                            if (replyResult) {
                                const replyAuthor = extractAuthor(replyResult);
                                const replyText = extractTweetText(replyResult);
                                replies.push(`  ↳ ${replyAuthor}: ${replyText}`);
                            }
                        } catch (e) {
                            // ignore missing items
                        }
                    }

                    if (replies.length > 0) {
                        repliesText = "\n\nTop replies:\n" + replies.join('\n');
                    }
                } else if (Array.isArray(responseBody.data)) {
                    // This is the direct `tweets` array structure
                    mainText = responseBody.data[0]?.text || "";
                    mainAuthor = "Twitter User";
                }

                // Push formatted text first
                const fullText = `Tweet by ${mainAuthor}:\n${mainText}${repliesText}`;
                if (fullText.trim()) {
                    contentArray.push({ type: 'text', text: fullText });
                }

                // ---- Extract Media ----
                const extractMediaFromIncludes = (includes: any) => {
                    if (includes && includes.media && Array.isArray(includes.media)) {
                        for (const m of includes.media) {
                            if (m.type === 'photo') {
                                contentArray.push({ type: 'image_url', image_url: { url: m.url } });
                                hasMedia = true;
                            } else if (m.type === 'video' || m.type === 'animated_gif') {
                                if (m.variants && Array.isArray(m.variants)) {
                                    // Get best mp4 variant
                                    const mp4Variants = m.variants.filter((v: any) => v.content_type === 'video/mp4');
                                    if (mp4Variants.length > 0) {
                                        const bestVariant = mp4Variants.reduce((prev: any, current: any) => {
                                            return ((prev.bit_rate || 0) > (current.bit_rate || 0)) ? prev : current;
                                        });
                                        contentArray.push({ type: 'video', video: { url: bestVariant.url } });
                                        hasMedia = true;
                                    }
                                }
                            }
                        }
                    }
                };

                const extractMediaFromExtended = (extended_entities: any) => {
                    if (extended_entities && extended_entities.media && Array.isArray(extended_entities.media)) {
                        for (const m of extended_entities.media) {
                            if (m.type === 'photo') {
                                contentArray.push({ type: 'image_url', image_url: { url: m.media_url_https } });
                                hasMedia = true;
                            } else if (m.type === 'video' || m.type === 'animated_gif') {
                                if (m.video_info && m.video_info.variants) {
                                    const mp4Variants = m.video_info.variants.filter((v: any) => v.content_type === 'video/mp4');
                                    if (mp4Variants.length > 0) {
                                        const bestVariant = mp4Variants.reduce((prev: any, current: any) => {
                                            return ((prev.bitrate || 0) > (current.bitrate || 0)) ? prev : current;
                                        });
                                        contentArray.push({ type: 'video', video: { url: bestVariant.url } });
                                        hasMedia = true;
                                    }
                                }
                            }
                        }
                    }
                };

                // Attempt both legacy and new extraction methods
                if (responseBody.includes) {
                    extractMediaFromIncludes(responseBody.includes);
                }

                if (isThreaded) {
                    const mainResult = responseBody.data.threaded_conversation_with_injections_v2.instructions[1].entries[0].content.itemContent.tweet_results.result;
                    if (mainResult?.legacy?.extended_entities) {
                        extractMediaFromExtended(mainResult.legacy.extended_entities);
                    }
                }
            } catch (e) {
                console.error("Failed to parse Twitter object payload:", e);
                contentArray = [{ type: 'text', text: JSON.stringify(responseBody, null, 2) }];
            }
        } else {
            contentArray = [{ type: 'text', text: typeof responseBody === 'string' ? responseBody : JSON.stringify(responseBody, null, 2) }];
        }

        messages.push({ role: 'assistant', content: contentArray.length === 1 && !hasMedia ? contentArray[0].text : contentArray });
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
                    <div key={idx} className={`flex w-full group ${isUser ? 'justify-start' : 'justify-end'}`}>
                        <div className={`relative max-w-[80%] rounded-2xl px-5 py-4 shadow-sm ${isUser ? 'bg-gray-800 text-gray-100 border border-gray-700/50' : 'bg-blue-900/30 border border-blue-500/20 text-gray-100'
                            }`}>

                            <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity">
                                <CopyButton
                                    text={typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content, null, 2)}
                                />
                            </div>

                            {msg.role && <div className="text-xs font-bold uppercase tracking-wider mb-2 opacity-50">{msg.role}</div>}
                            {renderMessageContent(msg.content, channelId)}
                        </div>
                    </div>
                );
            })}
        </div>
    );
};
