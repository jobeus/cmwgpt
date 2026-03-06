import { Terminal, Code, ExternalLink } from 'lucide-react';
import { sanitizeJsonForRawView } from '../utils/format';
import { CopyButton } from './CopyButton';

interface RawDetailPanelsProps {
    log: any;
    reqBody: any;
    reqHeaders: any;
    resBody: any;
    resHeaders: any;
}

export const RawDetailPanels = ({ log, reqBody, reqHeaders, resBody, resHeaders }: RawDetailPanelsProps) => {
    return (
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
                {/* Request Details */}
                <div className="bg-gray-950 border border-gray-800 rounded-xl overflow-hidden">
                    <div className="bg-gray-900 border-b border-gray-800 px-4 py-3 text-sm font-semibold text-gray-300 flex items-center">
                        <span className="w-2 h-2 rounded-full bg-blue-500 mr-2"></span>
                        Request Details
                    </div>
                    <div className="p-4 max-h-[70vh] overflow-y-auto">
                        {log.method && (
                            <div className="mb-4">
                                <div className="text-xs text-gray-500 uppercase mb-2 font-bold tracking-widest">Method</div>
                                <div className="inline-block text-sm font-mono font-bold px-3 py-1.5 rounded-lg border border-gray-800/60 bg-gray-900/50 text-purple-300">
                                    {log.method}
                                </div>
                            </div>
                        )}
                        {log.endpoint_url && (
                            <div className="mb-4">
                                <div className="text-xs text-gray-500 uppercase mb-2 font-bold tracking-widest">Endpoint</div>
                                <div className="flex items-center space-x-2 text-sm font-mono text-blue-300 bg-gray-900/50 p-3 rounded-lg border border-gray-800/60 break-all">
                                    <ExternalLink className="w-4 h-4 text-blue-400 flex-shrink-0" />
                                    <span>{log.endpoint_url}</span>
                                </div>
                            </div>
                        )}
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

                {/* Response Details */}
                <div className="bg-gray-950 border border-gray-800 rounded-xl overflow-hidden">
                    <div className="bg-gray-900 border-b border-gray-800 px-4 py-3 text-sm font-semibold text-gray-300 flex items-center">
                        <span className="w-2 h-2 rounded-full bg-emerald-500 mr-2"></span>
                        Response Details
                    </div>
                    <div className="p-4 max-h-[70vh] overflow-y-auto">
                        {log.response_status && (
                            <div className="mb-4">
                                <div className="text-xs text-gray-500 uppercase mb-2 font-bold tracking-widest">Status</div>
                                <div className={`inline-block text-sm font-mono font-bold px-3 py-1.5 rounded-lg border border-gray-800/60 bg-gray-900/50 ${log.response_status >= 400 ? 'text-red-400' : 'text-emerald-400'}`}>
                                    {log.response_status}
                                </div>
                            </div>
                        )}
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
    );
};
