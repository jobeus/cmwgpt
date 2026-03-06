import { useState } from 'react';
import { Copy, Check } from 'lucide-react';

export const CopyButton = ({ text, className = '' }: { text: string, className?: string }) => {
    const [copied, setCopied] = useState(false);

    const handleCopy = async () => {
        try {
            await navigator.clipboard.writeText(text);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        } catch (err) {
            console.error('Failed to copy text: ', err);
        }
    };

    return (
        <button
            onClick={handleCopy}
            title="Copy text"
            className={`p-1.5 rounded-md text-gray-400 hover:text-gray-200 hover:bg-gray-700/50 transition-colors ${className}`}
        >
            {copied ? <Check className="w-4 h-4 text-emerald-400" /> : <Copy className="w-4 h-4" />}
        </button>
    );
};
