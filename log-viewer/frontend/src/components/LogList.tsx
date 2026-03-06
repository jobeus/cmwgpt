import React, { useEffect, useState, useRef } from 'react';
import axios from 'axios';
import { io, Socket } from 'socket.io-client';
import { format } from 'date-fns';
import { useAuth } from '../AuthContext';
import { Copy, Terminal, ChevronRight, Server, Check, AlertCircle } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:3001/api';
const SOCKET_URL = import.meta.env.VITE_SOCKET_URL || 'http://localhost:3001';

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

const extractLastMessageSnippet = (bodyStr: string | null): string => {
    if (!bodyStr) return '';
    try {
        const parsed = JSON.parse(bodyStr);
        if (parsed.messages && Array.isArray(parsed.messages) && parsed.messages.length > 0) {
            const last = parsed.messages[parsed.messages.length - 1];
            let text = typeof last.content === 'string' ? last.content : JSON.stringify(last.content);
            if (text.length > 150) text = text.substring(0, 150) + '...';
            return text;
        }
        if (parsed.choices && parsed.choices[0] && parsed.choices[0].message) { // response
            let text = parsed.choices[0].message.content || '';
            if (text.length > 150) text = text.substring(0, 150) + '...';
            return text;
        }
    } catch (e) {
        // might be truncated json, just use regex or raw
    }

    // Fallback
    return bodyStr.length > 100 ? bodyStr.substring(0, 100) + '...' : bodyStr;
};

export default function LogList() {
    const [logs, setLogs] = useState<LogEntry[]>([]);
    const [total, setTotal] = useState(0);
    const [copiedId, setCopiedId] = useState<number | null>(null);
    const { token } = useAuth();
    const navigate = useNavigate();
    const socketRef = useRef<Socket | null>(null);

    useEffect(() => {
        fetchLogs();

        const socket = io(SOCKET_URL, {
            auth: { token }
        });

        socket.on('connect', () => console.log('WebSocket Connected'));
        socket.on('new-log', (newLog: LogEntry) => {
            setLogs((prev) => [newLog, ...prev]);
            setTotal((t) => t + 1);
        });

        socketRef.current = socket;

        return () => {
            socket.disconnect();
        };
    }, [token]);

    const fetchLogs = async () => {
        try {
            const res = await axios.get(`${API_BASE_URL}/logs?limit=50`);
            setLogs(res.data.logs);
            setTotal(res.data.total);
        } catch (err) {
            console.error('Failed to fetch logs', err);
        }
    };

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
                <h2 className="text-2xl font-bold bg-gradient-to-r from-white to-gray-400 bg-clip-text text-transparent">Recent Requests</h2>
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
                                    <span className="font-semibold text-white">{log.service_name}</span>
                                    <span className="text-gray-500 text-sm font-mono px-2 py-0.5 bg-gray-950 rounded border border-gray-800">{log.method}</span>
                                </div>
                            </div>
                            <div className="flex items-center space-x-4">
                                <span className="text-sm font-medium text-gray-400">
                                    {format(new Date(log.timestamp), 'MMM d, HH:mm:ss')}
                                </span>
                                {log.curl_command && (
                                    <button
                                        onClick={(e) => copyCurl(e, log.id, !log.curl_command ? null : log.curl_command)}
                                        className="flex items-center space-x-1.5 px-3 py-1.5 bg-gray-950 hover:bg-gray-700 text-gray-300 hover:text-white rounded-lg border border-gray-700 transition-colors z-10"
                                        title="Copy cURL command"
                                    >
                                        {copiedId === log.id ? <Check className="w-4 h-4 text-emerald-400" /> : <Terminal className="w-4 h-4" />}
                                        <span className="text-xs font-medium">{copiedId === log.id ? 'Copied' : 'cURL'}</span>
                                    </button>
                                )}
                                <ChevronRight className="w-5 h-5 text-gray-600 group-hover:text-blue-400 transition-colors" />
                            </div>
                        </div>

                        <div className="grid grid-cols-2 gap-4 text-sm mt-2">
                            <div className="bg-gray-950 rounded-lg p-3 border border-gray-800/60">
                                <div className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2 flex items-center">
                                    <span className="w-2 h-2 rounded-full bg-blue-500 mr-2"></span>
                                    Request
                                </div>
                                <div className="text-gray-300 font-mono text-xs truncate opacity-80">
                                    {extractLastMessageSnippet(log.request_body_snippet) || <span className="text-gray-600 italic">No body</span>}
                                </div>
                            </div>
                            <div className="bg-gray-950 rounded-lg p-3 border border-gray-800/60">
                                <div className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2 flex items-center">
                                    <span className="w-2 h-2 rounded-full bg-emerald-500 mr-2"></span>
                                    Response
                                </div>
                                <div className="text-gray-300 font-mono text-xs truncate opacity-80">
                                    {extractLastMessageSnippet(log.response_body_snippet) || <span className="text-gray-600 italic">No body</span>}
                                </div>
                            </div>
                        </div>

                        {Number(log.cost) > 0 && (
                            <div className="absolute bottom-4 right-14 text-xs font-medium text-amber-500/80 bg-amber-500/10 px-2 py-1 rounded border border-amber-500/20 pointer-events-none">
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
