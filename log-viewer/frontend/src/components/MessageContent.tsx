import { Image as ImageIcon, FileAudio, ExternalLink } from 'lucide-react';
import { parseDiscordPrefix, GUILD_ID } from '../utils/discord';
import { TweetCard } from './TweetCard';
import { AuthenticatedVideo } from './AuthenticatedMedia';

/**
 * Renders a single message's content based on its type:
 * - string: Discord-formatted text with user/timestamp parsing
 * - array: Multi-part content (images, video, audio, text)
 * - object: Tweet card or raw JSON fallback
 */
export const MessageContent = ({ content, channelId }: { content: any, channelId: string | null }) => {
    if (typeof content === 'string') {
        const parsed = parseDiscordPrefix(content);
        return (
            <div className="text-gray-200 text-sm whitespace-pre-wrap font-sans">
                {parsed.replyTo && (
                    <div className="mb-2 ml-1 pl-2 border-l-2 border-slate-600/50 flex flex-col gap-0.5 max-w-[90%]">
                        <div className="flex items-center gap-1.5 text-xs">
                            <span className="text-slate-500 font-bold select-none">↳</span>
                            {parsed.replyTo.userName && <span className="font-semibold text-blue-300 opacity-90">{parsed.replyTo.userName}</span>}
                            {parsed.replyTo.timestamp && <span className="text-[10px] text-slate-500">{parsed.replyTo.timestamp}</span>}
                        </div>
                        <div className="opacity-75 text-xs text-slate-300 line-clamp-2 overflow-hidden text-ellipsis ml-4">
                            {parsed.replyTo.content}
                        </div>
                    </div>
                )}
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
                        return <div key={idx} className="whitespace-pre-wrap"><MessageContent content={part.text} channelId={channelId} /></div>;
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
                        return (
                            <div key={idx} className="mt-2 text-sm text-gray-400 flex flex-col items-start border border-gray-800 bg-gray-900 rounded-lg p-3 inline-block max-w-[24rem]">
                                <div className="flex items-center mb-2">
                                    <ImageIcon className="w-4 h-4 mr-2 text-purple-400" />
                                    <span>Attached Video</span>
                                </div>
                                <AuthenticatedVideo
                                    src={part.video.url}
                                    className="w-full rounded-md border border-gray-700 bg-black max-h-64"
                                    loadingFallback={<div className="w-full h-40 rounded-md border border-gray-700 bg-black/60 animate-pulse" />}
                                />
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
        if (content.type === 'tweet') {
            return <TweetCard tweet={content} />;
        }

        return <div className="text-gray-400 text-xs font-mono whitespace-pre-wrap break-all">{JSON.stringify(content, null, 2)}</div>;
    }

    return <div className="text-red-400 text-xs">Unparseable content</div>;
};
