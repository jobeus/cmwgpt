import { parseServiceMessages } from '../parsers';
import { CopyButton } from './CopyButton';
import { MessageContent } from './MessageContent';

interface ConversationViewProps {
    requestBody: any;
    responseBody: any;
    channelId: string | null;
    serviceName: string;
    endpointUrl?: string | null;
}

export const ConversationView = ({ requestBody, responseBody, channelId, serviceName, endpointUrl }: ConversationViewProps) => {
    const messages = parseServiceMessages(serviceName, requestBody, responseBody, endpointUrl);

    if (messages.length === 0) {
        return (
            <div className="p-8 text-center text-gray-500 border border-dashed border-gray-800 rounded-xl my-4">
                Could not parse conversation from this request type. Try the Raw view.
            </div>
        );
    }

    return (
        <div className="space-y-6 my-4 w-full">
            {messages.map((msg, idx) => {
                const isUser = msg.role.includes('user') || msg.role.includes('request') || msg.role === 'system';
                return (
                    <div key={idx} className={`flex w-full group ${isUser ? 'justify-start' : 'justify-end'}`}>
                        <div className={`relative max-w-[80%] rounded-2xl px-5 py-4 shadow-sm ${isUser ? 'bg-gray-800 text-gray-100 border border-gray-700/50' : 'bg-blue-900/30 border border-blue-500/20 text-gray-100'
                            }`}>

                            <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity">
                                <CopyButton
                                    text={typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content, null, 2)}
                                />
                            </div>

                            {msg.role && <div className="text-xs font-bold uppercase tracking-wider mb-2 opacity-50">{msg.role}</div>}
                            <MessageContent content={msg.content} channelId={channelId} />
                        </div>
                    </div>
                );
            })}
        </div>
    );
};
