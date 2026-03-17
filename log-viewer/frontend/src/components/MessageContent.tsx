import { Image as ImageIcon, FileAudio, ExternalLink, Download } from 'lucide-react';
import { parseDiscordPrefix, GUILD_ID } from '../utils/discord';
import { getPipelineReplay } from '../utils/pipeline';
import { TweetCard } from './TweetCard';
import { AuthenticatedVideo } from './AuthenticatedMedia';
import { DataUrlViewer } from './DataUrlViewer';

const prettyLabel = (key: string) => key.replace(/_/g, ' ').replace(/\b\w/g, (m) => m.toUpperCase());

const inferArtifactTextContent = (artifact: Record<string, any>, payload: any): string | null => {
    const data = payload?.data;
    if (!data || typeof data !== 'object') return null;

    const preferredField = artifact.content_field;
    if (preferredField && typeof data[preferredField] === 'string') {
        return data[preferredField];
    }

    const artifactName = String(artifact.name || '').toLowerCase();
    const mediaType = String(artifact.media_type || '').toLowerCase();

    const candidateFields = [
        mediaType.includes('html') ? 'html' : null,
        artifactName.includes('transcript') ? 'transcript_text' : null,
        artifactName.includes('context') ? 'context' : null,
        artifactName.includes('aggregate') ? 'aggregated_content' : null,
        artifactName.includes('article') && mediaType.includes('plain') ? 'text' : null,
        'text',
        'transcript_text',
        'context',
        'aggregated_content',
        'html',
    ].filter(Boolean) as string[];

    for (const field of candidateFields) {
        if (typeof data[field] === 'string' && data[field]) {
            return data[field];
        }
    }

    return null;
};

const downloadArtifactText = (filename: string, mediaType: string, content: string) => {
    const blob = new Blob([content], { type: mediaType || 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename || 'artifact.txt';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
};

const PipelineDataView = ({ data }: { data: any }) => {
    if (data == null) return null;
    if (typeof data === 'string') {
        return <pre className="text-xs whitespace-pre-wrap text-gray-200 bg-black/30 rounded-lg p-3 border border-gray-800">{data}</pre>;
    }
    if (typeof data !== 'object') {
        return <div className="text-sm text-gray-200">{String(data)}</div>;
    }

    return (
        <div className="space-y-3">
            {Object.entries(data).map(([key, value]) => {
                if (value == null || value === '') return null;
                const isLongText = typeof value === 'string' && (value.includes('\n') || value.length > 180);
                const isNested = typeof value === 'object';

                return (
                    <div key={key}>
                        <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-1 font-semibold">{prettyLabel(key)}</div>
                        {isLongText ? (
                            <pre className="text-xs whitespace-pre-wrap text-gray-200 bg-black/30 rounded-lg p-3 border border-gray-800">{value as string}</pre>
                        ) : isNested ? (
                            <pre className="text-xs whitespace-pre-wrap text-gray-300 bg-black/30 rounded-lg p-3 border border-gray-800">{JSON.stringify(value, null, 2)}</pre>
                        ) : (
                            <div className="text-sm text-gray-200 break-all">{String(value)}</div>
                        )}
                    </div>
                );
            })}
        </div>
    );
};

const PipelineArtifacts = ({ artifacts, payload }: { artifacts?: Array<Record<string, any>>, payload: any }) => {
    if (!artifacts?.length) return null;
    return (
        <div className="grid gap-2 sm:grid-cols-2">
            {artifacts.map((artifact, idx) => {
                const filename = artifact.name || `Artifact ${idx + 1}`;
                const mediaType = artifact.media_type || 'unknown';
                const textContent = inferArtifactTextContent(artifact, payload);
                const sourceUrl = artifact.source_url || artifact.url || artifact.video_url;

                return (
                    <div key={idx} className="rounded-lg border border-gray-800 bg-black/20 p-3 text-xs text-gray-300">
                        <div className="font-semibold text-gray-100 mb-1">{filename}</div>
                        <div>{mediaType}</div>
                        {artifact.size_bytes != null && <div>{artifact.size_bytes} bytes</div>}
                        {artifact.sha256 && <div className="break-all text-gray-500 mt-1">sha256: {artifact.sha256}</div>}

                        {(textContent || sourceUrl) && (
                            <div className="flex flex-wrap gap-2 mt-3">
                                {textContent && (
                                    <button
                                        type="button"
                                        onClick={() => downloadArtifactText(filename, mediaType, textContent)}
                                        className="inline-flex items-center gap-1 rounded-md border border-gray-700 px-2 py-1 text-[11px] font-medium text-gray-100 hover:bg-gray-800 transition-colors"
                                    >
                                        <Download className="w-3 h-3" />
                                        Download
                                    </button>
                                )}
                                {sourceUrl && (
                                    <a
                                        href={sourceUrl}
                                        target="_blank"
                                        rel="noreferrer"
                                        className="inline-flex items-center gap-1 rounded-md border border-gray-700 px-2 py-1 text-[11px] font-medium text-gray-100 hover:bg-gray-800 transition-colors"
                                    >
                                        <ExternalLink className="w-3 h-3" />
                                        Source
                                    </a>
                                )}
                            </div>
                        )}
                    </div>
                );
            })}
        </div>
    );
};

const PipelineStepCard = ({ content }: { content: any }) => {
    const payload = content.payload;
    const replay = getPipelineReplay(payload);
    const badgeClass = content.side === 'input' ? 'text-blue-300 bg-blue-500/10 border-blue-500/20' : 'text-emerald-300 bg-emerald-500/10 border-emerald-500/20';

    return (
        <div className="space-y-4 min-w-[20rem] max-w-[48rem]">
            <div className="flex items-center justify-between gap-3">
                <div>
                    <div className="text-sm font-semibold text-white">{payload.title || payload.step || 'Pipeline step'}</div>
                    {payload.summary && <div className="text-xs text-gray-400 mt-1">{payload.summary}</div>}
                </div>
                <span className={`text-[11px] uppercase tracking-wider px-2 py-1 rounded border ${badgeClass}`}>{content.side}</span>
            </div>

            <PipelineDataView data={payload.data} />
            <PipelineArtifacts artifacts={payload.artifacts} payload={payload} />

            {replay && (
                <div>
                    <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-1 font-semibold">{replay.label}</div>
                    <pre className="text-xs whitespace-pre-wrap text-gray-300 bg-black/30 rounded-lg p-3 border border-gray-800">{replay.text}</pre>
                </div>
            )}
        </div>
    );
};

/**
 * Renders a single message's content based on its type:
 * - string: Discord-formatted text with user/timestamp parsing
 * - array: Multi-part content (images, video, audio, text)
 * - object: Tweet card or raw JSON fallback
 */
export const MessageContent = ({ content, channelId }: { content: any, channelId: string | null }) => {
    if (typeof content === 'string') {
        if (content.startsWith('data:')) {
            return <DataUrlViewer dataUrl={content} />;
        }
        
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

        if (content.type === 'pipeline_step') {
            return <PipelineStepCard content={content} />;
        }

        return <div className="text-gray-400 text-xs font-mono whitespace-pre-wrap break-all">{JSON.stringify(content, null, 2)}</div>;
    }

    return <div className="text-red-400 text-xs">Unparseable content</div>;
};
