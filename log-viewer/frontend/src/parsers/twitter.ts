import type { ServiceMessage } from './types';

// ---- Tweet data extraction helpers ----

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

const extractHandle = (result: any) => {
    return result?.core?.user_results?.result?.legacy?.screen_name || '';
};

const extractProfileImage = (result: any) => {
    return result?.core?.user_results?.result?.legacy?.profile_image_url_https || '';
};

const extractMediaFromIncludes = (includes: any, media: any[]) => {
    if (includes && includes.media && Array.isArray(includes.media)) {
        for (const m of includes.media) {
            if (m.type === 'photo') {
                media.push({ type: 'photo', url: m.url });
            } else if (m.type === 'video' || m.type === 'animated_gif') {
                if (m.variants && Array.isArray(m.variants)) {
                    const mp4Variants = m.variants.filter((v: any) => v.content_type === 'video/mp4');
                    if (mp4Variants.length > 0) {
                        const bestVariant = mp4Variants.reduce((prev: any, current: any) => {
                            return ((prev.bit_rate || 0) > (current.bit_rate || 0)) ? prev : current;
                        });
                        media.push({ type: 'video', url: bestVariant.url });
                    }
                }
            }
        }
    }
};

const extractMediaFromExtended = (extended_entities: any, media: any[]) => {
    if (extended_entities && extended_entities.media && Array.isArray(extended_entities.media)) {
        for (const m of extended_entities.media) {
            if (m.type === 'photo') {
                media.push({ type: 'photo', url: m.media_url_https });
            } else if (m.type === 'video' || m.type === 'animated_gif') {
                if (m.video_info && m.video_info.variants) {
                    const mp4Variants = m.video_info.variants.filter((v: any) => v.content_type === 'video/mp4');
                    if (mp4Variants.length > 0) {
                        const bestVariant = mp4Variants.reduce((prev: any, current: any) => {
                            return ((prev.bitrate || 0) > (current.bitrate || 0)) ? prev : current;
                        });
                        media.push({ type: 'video', url: bestVariant.url });
                    }
                }
            }
        }
    }
};

// ---- Main parser ----

export function parseTwitter(_requestBody: any, responseBody: any, endpointUrl?: string | null): ServiceMessage[] {
    const messages: ServiceMessage[] = [];

    // Extract tweet ID from endpoint URL and build a useful Python snippet
    let tweetId = '';
    let tweetUrl = '';
    if (endpointUrl) {
        try {
            const u = new URL(endpointUrl);
            tweetId = u.searchParams.get('id') || '';
        } catch { /* ignore */ }
    }
    if (tweetId) {
        tweetUrl = `https://x.com/i/status/${tweetId}`;
    }

    const pythonSnippet = `import httpx\n\nRAPIDAPI_KEY = "YOUR_RAPIDAPI_KEY"\ntweet_id = "${tweetId || 'TWEET_ID'}"\n\nheaders = {\n    "x-rapidapi-host": "x-com2.p.rapidapi.com",\n    "x-rapidapi-key": RAPIDAPI_KEY,\n}\n\nwith httpx.Client(timeout=15.0) as client:\n    response = client.get(\n        "https://x-com2.p.rapidapi.com/v2/TweetDetail/",\n        headers=headers,\n        params={"id": tweet_id},\n    )\n    response.raise_for_status()\n    data = response.json()\n\nprint(data)\n${tweetUrl ? `\n# Original tweet: ${tweetUrl}` : ''}`;

    messages.push({ role: 'system', content: pythonSnippet });

    // Try to format the JSON data into a custom 'tweet' object
    if (typeof responseBody === 'object' && responseBody?.data) {
        try {
            const isThreaded = responseBody.data.threaded_conversation_with_injections_v2 !== undefined;

            const tweetObj: any = {
                type: 'tweet',
                author: '',
                authorHandle: '',
                authorImage: '',
                text: '',
                media: [],
                replies: []
            };

            // ---- Extract text and Replies ----
            if (isThreaded) {
                const entries = responseBody.data.threaded_conversation_with_injections_v2.instructions[1].entries;
                // Main tweet
                const mainResult = entries[0].content.itemContent.tweet_results.result;
                tweetObj.author = extractAuthor(mainResult);
                tweetObj.authorHandle = extractHandle(mainResult);
                tweetObj.authorImage = extractProfileImage(mainResult);
                tweetObj.text = extractTweetText(mainResult);

                // Replies
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
                            const replyHandle = extractHandle(replyResult);
                            const replyImage = extractProfileImage(replyResult);
                            tweetObj.replies.push({ author: replyAuthor, text: replyText, authorHandle: replyHandle, authorImage: replyImage });
                        }
                    } catch {
                        // ignore missing items
                    }
                }
            } else if (Array.isArray(responseBody.data)) {
                // This is the direct `tweets` array structure
                tweetObj.text = responseBody.data[0]?.text || "";
                tweetObj.author = "Twitter User";
            }

            // ---- Extract Media ----
            if (responseBody.includes) {
                extractMediaFromIncludes(responseBody.includes, tweetObj.media);
            }

            if (isThreaded) {
                const mainResult = responseBody.data.threaded_conversation_with_injections_v2.instructions[1].entries[0].content.itemContent.tweet_results.result;
                if (mainResult?.legacy?.extended_entities) {
                    extractMediaFromExtended(mainResult.legacy.extended_entities, tweetObj.media);
                }
            }

            // If we successfully built a tweetObj with an author and text, wrap it as a single root message object
            if (tweetObj.text && tweetObj.author) {
                messages.push({ role: 'assistant', content: tweetObj });
            } else {
                messages.push({ role: 'assistant', content: [{ type: 'text', text: JSON.stringify(responseBody, null, 2) }] });
            }
        } catch (error) {
            console.error("Failed to parse Twitter object payload:", error);
            messages.push({ role: 'assistant', content: [{ type: 'text', text: JSON.stringify(responseBody, null, 2) }] });
        }
    } else {
        messages.push({ role: 'assistant', content: [{ type: 'text', text: typeof responseBody === 'string' ? responseBody : JSON.stringify(responseBody, null, 2) }] });
    }

    return messages;
}
