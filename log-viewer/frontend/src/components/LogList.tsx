import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { io, Socket } from 'socket.io-client';
import { format } from 'date-fns';
import { useAuth } from '../AuthContext';
import { Terminal, ChevronRight, Server, Check, AlertCircle } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import userMap from '../userMap';
import { getPipelineSnippet, getPipelineTitle, isPipelinePayload } from '../utils/pipeline';

const API_BASE_URL = import.meta.env.VITE_API_URL || '/api';
const SOCKET_URL = import.meta.env.VITE_SOCKET_URL || '/';

// Cap the in-memory list so a long-running tab doesn't grow without bound.
const MAX_LOG_ROWS = 300;

interface LogEntry {
    id: number;
    timestamp: string;
    service_name: string;
    method: string;
    endpoint_url: string;
    response_status: number;
    cost: number;
    request_body_snippet: string | null;
    response_body_snippet: string | null;
    curl_command: string | null;
}

interface ParsedLogEntry extends LogEntry {
    displayTitle: string;
    requestSnippet: string;
    responseSnippet: string;
}

const extractLastMessageSnippet = (bodyStr: string | null, serviceName: string, type: 'request' | 'response'): string => {
    if (!bodyStr) return '';
    try {
        const parsed = JSON.parse(bodyStr);

        if (isPipelinePayload(parsed)) {
            return getPipelineSnippet(parsed, type === 'request' ? '[Pipeline step input]' : '[Pipeline step output]');
        }

        if (serviceName.startsWith('runpod/')) {
            if (type === 'request') return parsed.input?.prompt || '[Image Gen Request]';
            if (type === 'response') return parsed.output?.image_url || parsed.output?.result || '[Generated Image URL]';
        }

        if (serviceName.startsWith('youtube/')) {
            if (type === 'request') {
                return '[Python Script] ' + parsed.split('\n')[0];
            }
            if (type === 'response') {
                let text = typeof parsed === 'string' ? parsed : JSON.stringify(parsed);
                if (text.length > 150) text = text.substring(0, 150) + '...';
                return text;
            }
        }

        if (serviceName.startsWith('groq/whisper')) {
            if (type === 'request') {
                return '[Audio Upload (multipart/form-data)]';
            }
            if (type === 'response') {
                let text = typeof parsed === 'string' ? parsed : JSON.stringify(parsed);
                if (text.length > 150) text = text.substring(0, 150) + '...';
                return text;
            }
        }

        if (serviceName.startsWith('rapidapi/twitter')) {
            if (type === 'request') return 'Fetching Twitter/X thread context';
            if (type === 'response') return `[Twitter Data JSON Payload]`;
        }

        if (serviceName.startsWith('url_utils/')) {
            if (type === 'request') {
                if (typeof parsed === 'string' && parsed.includes('import')) {
                    return '[Python Script] ' + parsed.split('\n')[0];
                }
                return 'Scraping article content';
            }
            if (type === 'response') {
                const text = typeof parsed === 'string' ? parsed : JSON.stringify(parsed);
                return text.length > 150 ? text.substring(0, 150) + '...' : text;
            }
        }

        // Default OpenAI / Anthropic format
        if (type === 'request') {
            if (parsed.messages && Array.isArray(parsed.messages) && parsed.messages.length > 0) {
                const last = parsed.messages[parsed.messages.length - 1];
                let text = '';
                if (Array.isArray(last.content)) {
                    const textParts = last.content.filter((p: any) => p.type === 'text');
                    text = textParts.length > 0 ? textParts.map((p: any) => p.text).join(' ') : '[Complex Content / Image / Audio]';
                } else if (typeof last.content === 'string') {
                    text = last.content;
                } else {
                    text = JSON.stringify(last.content);
                }

                // Clean up Discord prefix and mentions
                const discordRegex = /^\[.*?\] \[\d+\] <@(\d+)>:\s*([\s\S]*)/;
                const match = text.match(discordRegex);
                if (match) {
                    const userId = match[1];
                    const content = match[2];
                    const username = userMap[userId] || userId;
                    text = `@${username}: ${content}`;
                }
                text = text.replace(/<@(\d+)>/g, (_m, id) => {
                    const username = userMap[id];
                    return username ? `@${username}` : _m;
                });
                return text.length > 150 ? text.substring(0, 150) + '...' : text;
            }
        }

        if (type === 'response') {
            if (parsed.choices && parsed.choices[0] && parsed.choices[0].message) {
                let text = '';
                const content = parsed.choices[0].message.content;
                if (Array.isArray(content)) {
                    const textPart = content.find((p: any) => p.type === 'text');
                    if (textPart) text = textPart.text;
                } else if (typeof content === 'string') {
                    text = content;
                } else {
                    text = JSON.stringify(content);
                }
                text = text.replace(/<@(\d+)>/g, (_m, id) => {
                    const username = userMap[id];
                    return username ? `@${username}` : _m;
                });
                return text.length > 150 ? text.substring(0, 150) + '...' : text;
            }
        }

        // Fallback for json objects that don't match our shapes (like raw rapidapi payload)
        return '[JSON Object]';
    } catch {
        // Fallback for non-json (e.g. raw text articles)
        return bodyStr.length > 150 ? bodyStr.substring(0, 150) + '...' : bodyStr;
    }
};

// Parse body JSON once when a log is received instead of on every render.
const parseLogEntry = (log: LogEntry): ParsedLogEntry => {
    let displayTitle = log.service_name;
    try {
        displayTitle = getPipelineTitle(
            log.request_body_snippet ? JSON.parse(log.request_body_snippet) : null,
            log.response_body_snippet ? JSON.parse(log.response_body_snippet) : null,
            log.service_name,
        );
    } catch {
        // Fall back to the service name for unparseable bodies.
    }

    return {
        ...log,
        displayTitle,
        requestSnippet: extractLastMessageSnippet(log.request_body_snippet, log.service_name, 'request'),
        responseSnippet: extractLastMessageSnippet(log.response_body_snippet, log.service_name, 'response'),
    };
};

export default function LogList() {
    const [logs, setLogs] = useState<ParsedLogEntry[]>([]);
    const [total, setTotal] = useState(0);
    const [copiedId, setCopiedId] = useState<number | null>(null);
    const { token } = useAuth();
    const navigate = useNavigate();
    const socketRef = useRef<Socket | null>(null);
    // Track ids we've already shown so socket/fetch overlap can't create duplicate rows.
    const seenLogIdsRef = useRef<Set<number>>(new Set());

    const fetchLogs = useCallback(async () => {
        try {
            const res = await axios.get(`${API_BASE_URL}/logs?limit=50`);
            const parsedLogs: ParsedLogEntry[] = res.data.logs.map(parseLogEntry);
            seenLogIdsRef.current = new Set(parsedLogs.map((log) => log.id));
            setLogs(parsedLogs);
            setTotal(res.data.total);
        } catch (err) {
            console.error('Failed to fetch logs', err);
        }
    }, []);

    useEffect(() => {
        fetchLogs();

        const socket = io(SOCKET_URL, {
            auth: { token }
        });

        socket.on('connect', () => console.log('WebSocket Connected'));
        socket.on('new-log', (newLog: LogEntry) => {
            if (seenLogIdsRef.current.has(newLog.id)) return;
            seenLogIdsRef.current.add(newLog.id);
            const parsedLog = parseLogEntry(newLog);
            setLogs((prev) => [parsedLog, ...prev].slice(0, MAX_LOG_ROWS));
            setTotal((t) => t + 1);
        });

        socketRef.current = socket;

        return () => {
            socket.disconnect();
        };
    }, [fetchLogs, token]);

    const copyCurl = (e: React.MouseEvent, id: number, curl: string | null) => {
        e.stopPropagation();
        if (!curl) return;
        navigator.clipboard.writeText(curl);
        setCopiedId(id);
        setTimeout(() => setCopiedId(null), 2000);
    };

    const getStatusColor = (status: number) => {
        if (status >= 200 && status < 300) return 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20';
        if (status >= 400 && status < 500) return 'text-amber-400 bg-amber-400/10 border-amber-400/20';
        if (status >= 500) return 'text-red-400 bg-red-400/10 border-red-400/20';
        return 'text-gray-400 bg-gray-400/10 border-gray-400/20';
    };

    return (
        <div className="space-y-6 pb-20">
            <div className="flex justify-between items-center">
                <h2 className="text-2xl font-bold bg-gradient-to-r from-white to-gray-400 bg-clip-text text-transparent">Recent Logs</h2>
                <span className="bg-gray-800 text-gray-300 py-1 px-3 rounded-full text-sm font-medium border border-gray-700">
                    Showing {logs.length} of {total}
                </span>
            </div>

            <div className="flex flex-col space-y-3">
                {logs.map((log) => (
                    <div
                        key={log.id}
                        onClick={() => navigate(`/logs/${log.id}`)}
                        className="group bg-gray-900 border border-gray-800 rounded-xl p-4 hover:border-blue-500/50 hover:bg-gray-800/80 transition-all cursor-pointer shadow-sm hover:shadow-blue-500/10 relative overflow-hidden"
                    >
                        <div className="flex items-center justify-between mb-3">
                            <div className="flex items-center space-x-3">
                                <span className={`px-2.5 py-1 rounded border text-xs font-bold font-mono tracking-wide ${getStatusColor(log.response_status)}`}>
                                    {log.response_status || '---'}
                                </span>
                                <div className="flex items-center space-x-2 text-gray-300">
                                    <Server className="w-4 h-4 text-blue-400" />
                                    <div>
                                        <div className="font-semibold text-white">{log.displayTitle}</div>
                                        {log.displayTitle !== log.service_name && <div className="text-[11px] text-gray-500 font-mono">{log.service_name}</div>}
                                    </div>
                                    <span className="text-gray-500 text-sm font-mono px-2 py-0.5 bg-gray-950 rounded border border-gray-800">{log.method}</span>
                                </div>
                            </div>
                            <div className="flex items-center space-x-4 pr-24">
                                <span className="text-sm font-medium text-gray-400">
                                    {format(new Date(log.timestamp), 'MMM d, HH:mm:ss')}
                                </span>
                                {log.method === 'PYTHON' ? (
                                    <button
                                        onClick={(e) => copyCurl(e, log.id, log.request_body_snippet)}
                                        className="flex items-center space-x-1.5 px-3 py-1.5 bg-blue-950/40 hover:bg-blue-900/60 text-blue-300 hover:text-white rounded-lg border border-blue-800/50 transition-colors z-10"
                                        title="Copy Python snippet"
                                    >
                                        {copiedId === log.id ? <Check className="w-4 h-4 text-emerald-400" /> : <Terminal className="w-4 h-4" />}
                                        <span className="text-xs font-medium">{copiedId === log.id ? 'Copied' : 'Python'}</span>
                                    </button>
                                ) : log.curl_command && (
                                    <button
                                        onClick={(e) => copyCurl(e, log.id, log.curl_command)}
                                        className="flex items-center space-x-1.5 px-3 py-1.5 bg-gray-950 hover:bg-gray-700 text-gray-300 hover:text-white rounded-lg border border-gray-700 transition-colors z-10"
                                        title="Copy cURL command"
                                    >
                                        {copiedId === log.id ? <Check className="w-4 h-4 text-emerald-400" /> : <Terminal className="w-4 h-4" />}
                                        <span className="text-xs font-medium">{copiedId === log.id ? 'Copied' : 'cURL'}</span>
                                    </button>
                                )}
                                <ChevronRight className="w-5 h-5 text-gray-600 group-hover:text-blue-400 transition-colors -mr-2" />
                            </div>
                        </div>

                        <div className="grid grid-cols-2 gap-4 text-sm mt-2">
                            <div className="bg-gray-950 rounded-lg p-3 border border-gray-800/60">
                                <div className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2 flex items-center">
                                    <span className="w-2 h-2 rounded-full bg-blue-500 mr-2"></span>
                                    Request
                                </div>
                                <div className="text-gray-300 font-mono text-xs truncate opacity-80">
                                    {log.requestSnippet || <span className="text-gray-600 italic">No body</span>}
                                </div>
                            </div>
                            <div className="bg-gray-950 rounded-lg p-3 border border-gray-800/60 overflow-hidden">
                                <div className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2 flex items-center">
                                    <span className="w-2 h-2 rounded-full bg-emerald-500 mr-2"></span>
                                    Response
                                </div>
                                <div className="text-gray-300 font-mono text-xs truncate opacity-80">
                                    {log.responseSnippet || <span className="text-gray-600 italic">No body</span>}
                                </div>
                            </div>
                        </div>

                        {Number(log.cost) > 0 && (
                            <div className="absolute top-4 right-4 text-xs font-medium text-amber-500/80 bg-amber-500/10 px-2 py-1 rounded border border-amber-500/20 pointer-events-none">
                                ${Number(log.cost).toFixed(5)}
                            </div>
                        )}
                    </div>
                ))}
                {logs.length === 0 && (
                    <div className="text-center py-20 text-gray-500 border border-dashed border-gray-700 rounded-xl">
                        <AlertCircle className="w-8 h-8 mx-auto mb-3 text-gray-600" />
                        <p>No requests found in the database.</p>
                    </div>
                )}
            </div>
        </div>
    );
}
