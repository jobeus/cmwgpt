import userMap from '../userMap';

export { userMap };
export const GUILD_ID = import.meta.env.VITE_DISCORD_GUILD_ID || '1120463633693024346'; // fallback for demo

export interface ParsedMessage {
    timestamp?: string;
    msgId?: string;
    userId?: string;
    userName?: string;
    content: string;
    replyTo?: ParsedMessage;
}

export const parseDiscordPrefix = (text: string): ParsedMessage => {
    let replyTo: ParsedMessage | undefined = undefined;
    let contentToParse = text;

    // Match the [Replying to message...] block that mention handler prepends
    const replyMatch = contentToParse.match(/^\[Replying to message(?: ID)?: ([\s\S]*?)\]\n\n/);
    if (replyMatch) {
        let replyRaw = replyMatch[1];
        if (replyRaw.startsWith('"') && replyRaw.endsWith('"')) {
            replyRaw = replyRaw.substring(1, replyRaw.length - 1);
        }
        replyTo = parseDiscordPrefix(replyRaw);
        contentToParse = contentToParse.substring(replyMatch[0].length);
    }

    // Regex matches: [2026-03-04 13:13:17] [1478742130095554613] <@392013989930074127>: message text
    // We specifically look for a date-like pattern or restrict from matching another bracket
    const regex = /^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[(\d+)\] <@(\d+)>:\s*([\s\S]*)/;
    const match = contentToParse.match(regex);

    if (match) {
        return {
            timestamp: match[1],
            msgId: match[2],
            userId: match[3],
            userName: userMap[match[3]] || match[3], // map to username if exists
            content: match[4],
            replyTo
        };
    }
    return { content: contentToParse, replyTo };
};
