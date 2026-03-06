import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import axios from 'axios';
import { ArrowLeft, Terminal, Code, MessageSquare, Clock, Server, Hash } from 'lucide-react';
import { format } from 'date-fns';
import { ConversationView, sanitizeJsonForRawView, CopyButton } from './MessageParser';

const API_BASE_URL = import.meta.env.VITE_API_URL || '/api';

export default function LogDetail() {
    const { id } = useParams<{ id: string }>();
    const navigate = useNavigate();
    const [log, setLog] = useState<any>(null);
    const [rawMode, setRawMode] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        const fetchLog = async () => {
            try {
                const res = await axios.get(`${API_BASE_URL}/logs/${id}`);
                setLog(res.data);
            } catch (err) {
                setError('Failed to load log details');
            }
        };
        fetchLog();
    }, [id]);

    if (error) return <div className="p-8 text-red-400">{error}</div>;
    if (!log) return <div className="p-8 text-gray-400 flex justify-center mt-20">Loading details...</div>;

    const parseJsonSafe = (str: string | null) => {
        if (!str) return null;
        try { return JSON.parse(str); } catch (e) { return str; }
    };

    const reqBody = parseJsonSafe(log.request_body);
    const reqHeaders = parseJsonSafe(log.request_headers);
    const resBody = parseJsonSafe(log.response_body);
    const resHeaders = parseJsonSafe(log.response_headers);

    return (
        <div className="pb-20">
            <div className="flex items-center justify-between mb-6">
                <button
                    onClick={() => navigate(-1)}
                    className="flex items-center space-x-2 text-gray-400 hover:text-white transition-colors py-2 px-3 bg-gray-900 rounded-lg border border-gray-800 hover:border-gray-700"
                >
                    <ArrowLeft className="w-4 h-4" />
                    <span className="text-sm font-medium">Back to Logs</span>
                </button>

                <div className="flex items-center bg-gray-900 rounded-lg p-1 border border-gray-800">
                    <button
                        onClick={() => setRawMode(false)}
                        className={`flex items-center space-x-2 px-4 py-2 rounded-md transition-all ${!rawMode ? 'bg-blue-600/20 text-blue-400 border border-blue-500/30' : 'text-gray-500 hover:text-gray-300'}`}
                    >
                        <MessageSquare className="w-4 h-4" />
                        <span className="text-sm font-semibold">Conversation</span>
                    </button>
                    <button
                        onClick={() => setRawMode(true)}
                        className={`flex items-center space-x-2 px-4 py-2 rounded-md transition-all ${rawMode ? 'bg-gray-800 text-white border border-gray-700' : 'text-gray-500 hover:text-gray-300'}`}
                    >
                        <Code className="w-4 h-4" />
                        <span className="text-sm font-semibold">Raw Data</span>
                    </button>
                </div>
            </div>

            <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 shadow-xl mb-6 flex flex-wrap gap-y-4 justify-between items-center">
                <div className="flex items-center space-x-6">
                    <div>
                        <div className="text-xs text-gray-500 uppercase font-bold tracking-wider mb-1">Service</div>
                        <div className="flex items-center space-x-2 text-white">
                            <Server className="w-4 h-4 text-blue-400" />
                            <span className="font-semibold text-lg">{log.service_name}</span>
                        </div>
                    </div>
                    <div>
                        <div className="text-xs text-gray-500 uppercase font-bold tracking-wider mb-1">Status</div>
                        <div className={`text-lg font-mono font-bold ${log.response_status >= 400 ? 'text-red-400' : 'text-emerald-400'}`}>
                            {log.response_status || 'N/A'}
                        </div>
                    </div>
                    <div>
                        <div className="text-xs text-gray-500 uppercase font-bold tracking-wider mb-1">Time</div>
                        <div className="flex items-center space-x-2 text-gray-300">
                            <Clock className="w-4 h-4 text-gray-500" />
                            <span>{format(new Date(log.timestamp), 'MMM d, yyyy HH:mm:ss')}</span>
                        </div>
                    </div>
                </div>

                <div className="flex items-center space-x-6">
                    {Number(log.cost) > 0 && (
                        <div className="text-right">
                            <div className="text-xs text-amber-500/70 uppercase font-bold tracking-wider mb-1">Cost</div>
                            <div className="text-lg font-mono text-amber-400 font-bold">${Number(log.cost).toFixed(5)}</div>
                        </div>
                    )}
                    {log.discord_channel_id && (
                        <div className="text-right flex flex-col items-end border-l border-gray-800 pl-6">
                            <div className="text-xs text-indigo-400/70 uppercase font-bold tracking-wider mb-1">Discord Channel</div>
                            <div className="flex items-center text-sm font-mono text-indigo-300 bg-indigo-500/10 px-2 py-1 rounded">
                                <Hash className="w-3 h-3 mr-1" />
                                {log.discord_channel_id}
                            </div>
                        </div>
                    )}
                </div>
            </div>

            {!rawMode ? (
                <div className="bg-[#0f1115] border border-gray-800 rounded-xl p-6 min-h-[50vh]">
                    <ConversationView
                        requestBody={reqBody}
                        responseBody={resBody}
                        channelId={log.discord_channel_id ? log.discord_channel_id.toString() : null}
                        serviceName={log.service_name}
                    />
                </div>
            ) : (
                <div className="space-y-6">
                    {log.method === 'PYTHON' ? (
                        <div className="bg-gray-950 border border-gray-800 rounded-xl overflow-hidden relative group">
                            <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity z-10">
                                <CopyButton text={reqBody} className="bg-gray-900 border border-gray-700 shadow pl-2 pr-2" />
                            </div>
                            <div className="bg-blue-900/30 border-b border-gray-800 px-4 py-3 flex items-center shadow-lg">
                                <Code className="w-4 h-4 text-blue-400 mr-2" />
                                <span className="text-sm font-semibold text-blue-300">Executable Python Snippet</span>
                            </div>
                            <div className="p-4 overflow-x-auto text-xs font-mono text-gray-300 bg-black/50 leading-relaxed whitespace-pre-wrap">
                                {reqBody}
                            </div>
                        </div>
                    ) : log.curl_command && (
                        <div className="bg-gray-950 border border-gray-800 rounded-xl overflow-hidden relative group">
                            <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity z-10">
                                <CopyButton text={log.curl_command} className="bg-gray-900 border border-gray-700 shadow pl-2 pr-2" />
                            </div>
                            <div className="bg-gray-900 border-b border-gray-800 px-4 py-3 flex items-center shadow-lg">
                                <Terminal className="w-4 h-4 text-blue-400 mr-2" />
                                <span className="text-sm font-semibold text-gray-300">Generated cURL</span>
                            </div>
                            <div className="p-4 overflow-x-auto text-xs font-mono text-emerald-400 bg-black/50 leading-relaxed">
                                {log.curl_command}
                            </div>
                        </div>
                    )}

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <div className="bg-gray-950 border border-gray-800 rounded-xl overflow-hidden">
                            <div className="bg-gray-900 border-b border-gray-800 px-4 py-3 text-sm font-semibold text-gray-300 flex items-center">
                                <span className="w-2 h-2 rounded-full bg-blue-500 mr-2"></span>
                                Request Details
                            </div>
                            <div className="p-4 max-h-[70vh] overflow-y-auto">
                                {reqHeaders && (
                                    <div className="mb-4">
                                        <div className="text-xs text-gray-500 uppercase mb-2 font-bold tracking-widest">Headers</div>
                                        <pre className="text-xs font-mono text-gray-300 bg-gray-900/50 p-3 rounded-lg border border-gray-800/60">
                                            {JSON.stringify(reqHeaders, null, 2)}
                                        </pre>
                                    </div>
                                )}
                                <div>
                                    <div className="text-xs text-gray-500 uppercase mb-2 font-bold tracking-widest flex justify-between items-center group">
                                        <span>Body</span>
                                        <div className="opacity-0 group-hover:opacity-100 transition-opacity">
                                            <CopyButton text={typeof reqBody === 'object' ? JSON.stringify(sanitizeJsonForRawView(reqBody), null, 2) : reqBody} />
                                        </div>
                                    </div>
                                    <pre className="text-xs font-mono text-gray-300 bg-gray-900/50 p-3 rounded-lg border border-gray-800/60 overflow-x-auto whitespace-pre-wrap break-all">
                                        {typeof reqBody === 'object' ? JSON.stringify(sanitizeJsonForRawView(reqBody), null, 2) : reqBody}
                                    </pre>
                                </div>
                            </div>
                        </div>

                        <div className="bg-gray-950 border border-gray-800 rounded-xl overflow-hidden">
                            <div className="bg-gray-900 border-b border-gray-800 px-4 py-3 text-sm font-semibold text-gray-300 flex items-center">
                                <span className="w-2 h-2 rounded-full bg-emerald-500 mr-2"></span>
                                Response Details
                            </div>
                            <div className="p-4 max-h-[70vh] overflow-y-auto">
                                {resHeaders && (
                                    <div className="mb-4">
                                        <div className="text-xs text-gray-500 uppercase mb-2 font-bold tracking-widest">Headers</div>
                                        <pre className="text-xs font-mono text-emerald-200/80 bg-gray-900/50 p-3 rounded-lg border border-gray-800/60">
                                            {JSON.stringify(resHeaders, null, 2)}
                                        </pre>
                                    </div>
                                )}
                                <div>
                                    <div className="text-xs text-gray-500 uppercase mb-2 font-bold tracking-widest flex justify-between items-center group">
                                        <span>Body</span>
                                        <div className="opacity-0 group-hover:opacity-100 transition-opacity">
                                            <CopyButton text={typeof resBody === 'object' ? JSON.stringify(sanitizeJsonForRawView(resBody), null, 2) : resBody} />
                                        </div>
                                    </div>
                                    <pre className="text-xs font-mono text-emerald-200/80 bg-gray-900/50 p-3 rounded-lg border border-gray-800/60 overflow-x-auto whitespace-pre-wrap break-all">
                                        {typeof resBody === 'object' ? JSON.stringify(sanitizeJsonForRawView(resBody), null, 2) : resBody}
                                    </pre>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
