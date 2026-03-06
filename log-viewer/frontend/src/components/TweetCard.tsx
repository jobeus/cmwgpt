import { CopyButton } from './CopyButton';
import { AuthenticatedImage, AuthenticatedVideo } from './AuthenticatedMedia';

interface TweetData {
    type: 'tweet';
    author: string;
    authorHandle?: string;
    authorImage?: string;
    text: string;
    media?: { type: string; url: string }[];
    replies?: {
        author: string;
        text: string;
        authorHandle?: string;
        authorImage?: string;
    }[];
}

export const TweetCard = ({ tweet }: { tweet: TweetData }) => {
    const replyContent = tweet.replies && tweet.replies.length > 0 ? tweet.replies.map(r => r.text).join('\n') : "";
    const rawCopyText = `Tweet by ${tweet.author}:\n${tweet.text}${replyContent ? `\n\nReplies:\n${replyContent}` : ''}`;

    return (
        <div className="w-full" title={rawCopyText}>
            <div className="bg-[#15202b] border border-[#38444d] rounded-xl overflow-hidden shadow-xl max-w-xl self-start group">

                {/* Main Tweet Area */}
                <div className="p-4 relative">
                    {/* Small absolute copy button specifically for the tweet box context */}
                    <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity z-10">
                        <CopyButton text={rawCopyText} className="bg-[#15202b]/80 border border-[#38444d] p-1 shadow" />
                    </div>

                    <div className="flex items-center mb-3">
                        {tweet.authorImage ? (
                            <AuthenticatedImage
                                src={tweet.authorImage}
                                alt={tweet.author}
                                stripQuery={false}
                                className="w-10 h-10 rounded-full mr-3 object-cover"
                                fallback={
                                    <div className="w-10 h-10 rounded-full bg-gradient-to-tr from-blue-500 to-indigo-600 flex items-center justify-center text-white font-bold text-lg mr-3 shadow-inner">
                                        {tweet.author.charAt(0).toUpperCase()}
                                    </div>
                                }
                            />
                        ) : (
                            <div className="w-10 h-10 rounded-full bg-gradient-to-tr from-blue-500 to-indigo-600 flex items-center justify-center text-white font-bold text-lg mr-3 shadow-inner">
                                {tweet.author.charAt(0).toUpperCase()}
                            </div>
                        )}
                        <div className="flex flex-col">
                            <span className="text-white font-bold leading-tight">{tweet.author}</span>
                            <span className="text-[#8899a6] text-sm leading-tight">{tweet.authorHandle ? `@${tweet.authorHandle}` : '@twitter_user'}</span>
                        </div>

                        {/* X icon SVG inline */}
                        <div className="ml-auto text-[#8899a6]">
                            <svg viewBox="0 0 24 24" aria-hidden="true" className="w-5 h-5 fill-current">
                                <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 22.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"></path>
                            </svg>
                        </div>
                    </div>

                    <div className="text-white text-base font-normal leading-normal whitespace-pre-wrap mb-3 font-sans break-words">
                        {tweet.text}
                    </div>

                    {tweet.media && tweet.media.length > 0 && (
                        <div className="rounded-xl overflow-hidden border border-[#38444d] mt-3 mb-2 max-h-[400px]">
                            {tweet.media.map((mediaId: any, i: number) => (
                                <div key={i} className="w-full h-full flex items-center justify-center bg-black">
                                    {mediaId.type === 'video' ? (
                                        <AuthenticatedVideo
                                            src={mediaId.url}
                                            className="max-w-full max-h-[400px] object-contain"
                                            loadingFallback={<div className="w-[24rem] h-64 max-w-full bg-gray-900 animate-pulse" />}
                                        />
                                    ) : (
                                        <AuthenticatedImage
                                            src={mediaId.url}
                                            alt="Tweet Attachment"
                                            stripQuery={false}
                                            className="max-w-full max-h-[400px] object-contain"
                                            loadingFallback={<div className="w-[24rem] h-64 max-w-full bg-gray-900 animate-pulse" />}
                                        />
                                    )}
                                </div>
                            ))}
                        </div>
                    )}

                    <div className="text-[#8899a6] text-sm mt-3 pt-3 border-t border-[#38444d]">
                        {new Date().toLocaleDateString('en-US', { hour: 'numeric', minute: 'numeric', year: 'numeric', month: 'short', day: 'numeric' })}
                    </div>
                </div>

                {/* Replies Section */}
                {tweet.replies && tweet.replies.length > 0 && (
                    <div className="bg-[#192734] border-t border-[#38444d] p-4 pl-14">
                        <div className="text-[#1da1f2] text-xs font-bold uppercase tracking-wider mb-3">Top Replies</div>
                        <div className="space-y-4">
                            {tweet.replies.map((reply: any, idx: number) => (
                                <div key={idx} className="flex relative">
                                    {idx !== tweet.replies!.length - 1 && (
                                        <div className="absolute left-[-24px] top-6 bottom-[-24px] w-0.5 bg-[#38444d]"></div>
                                    )}
                                    {reply.authorImage ? (
                                        <AuthenticatedImage
                                            src={reply.authorImage}
                                            alt={reply.author}
                                            stripQuery={false}
                                            className="absolute left-[-28px] top-0 w-8 h-8 rounded-full object-cover"
                                            fallback={
                                                <div className="absolute left-[-28px] top-0 w-8 h-8 rounded-full bg-gray-700 flex items-center justify-center text-xs font-bold text-gray-300">
                                                    {reply.author.charAt(0).toUpperCase()}
                                                </div>
                                            }
                                        />
                                    ) : (
                                        <div className="absolute left-[-28px] top-0 w-8 h-8 rounded-full bg-gray-700 flex items-center justify-center text-xs font-bold text-gray-300">
                                            {reply.author.charAt(0).toUpperCase()}
                                        </div>
                                    )}
                                    <div className="flex flex-col ml-1">
                                        <div className="text-white font-bold text-sm">{reply.author} <span className="text-[#8899a6] font-normal">{reply.authorHandle ? `@${reply.authorHandle}` : '@reply_user'}</span></div>
                                        <div className="text-gray-300 text-sm whitespace-pre-wrap mt-1 leading-normal break-words">{reply.text}</div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};
