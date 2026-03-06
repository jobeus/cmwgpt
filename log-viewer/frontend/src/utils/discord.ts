import userMap from '../userMap';

export { userMap };
export const GUILD_ID = import.meta.env.VITE_DISCORD_GUILD_ID || '1120463633693024346'; // fallback for demo

export interface ParsedMessage {
    timestamp?: string;
    msgId?: string;
    userId?: string;
    userName?: string;
    content: string;
}

export const parseDiscordPrefix = (text: string): ParsedMessage => {
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
