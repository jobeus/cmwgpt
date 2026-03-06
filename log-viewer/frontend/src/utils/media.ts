// Helper to proxy media URLs (videos, images) through our backend to bypass CORS
export const API_BASE_URL = import.meta.env.VITE_API_URL || 'https://polar.jobe.wtf/api';

export const proxyMediaUrl = (url: string, stripQuery = true) => {
    try {
        const u = new URL(url);
        const finalUrl = stripQuery ? `${u.origin}${u.pathname}` : url;
        return `${API_BASE_URL}/proxy-media?url=${encodeURIComponent(finalUrl)}`;
    } catch {
        const base = stripQuery ? url.split('?')[0] : url;
        return `${API_BASE_URL}/proxy-media?url=${encodeURIComponent(base)}`;
    }
};

// Helper to extract audio binary from a raw hex payload
export const extractAudioFromHex = (hexString: string): string | null => {
    try {
        if (!hexString || hexString.length < 100) return null;

        // Quick peek at the first few characters to see if it even looks like hex
        // We avoid heavy regex on 10MB strings because it crashes Safari/iOS and some V8 limits
        const head = hexString.substring(0, 100);
        if (!/^[0-9a-fA-F]+$/.test(head)) return null;

        // Convert hex to Uint8Array
        const bytes = new Uint8Array(hexString.length / 2);
        for (let i = 0; i < hexString.length; i += 2) {
            bytes[i / 2] = parseInt(hexString.substring(i, i + 2), 16);
        }

        // Check if there are multipart boundaries
        const decoder = new TextDecoder('latin1');
        const headStr = decoder.decode(bytes.subarray(0, 500));

        let audioBytes = bytes;

        // If it contains a filename boundary, extract just the binary part
        const fileContentIdx = headStr.indexOf('filename=');
        if (fileContentIdx !== -1) {
            const str = decoder.decode(bytes);
            const headerEndIdx = str.indexOf('\r\n\r\n', fileContentIdx);
            if (headerEndIdx !== -1) {
                const binaryStartIndex = headerEndIdx + 4;
                const nextBoundaryIdx = str.indexOf('\r\n--', binaryStartIndex);
                if (nextBoundaryIdx !== -1) {
                    audioBytes = bytes.slice(binaryStartIndex, nextBoundaryIdx);
                }
            }
        } else {
            // Otherwise, it's just a raw audio file (e.g. ID3 header '494433' or MPEG sync word 'fffb')
            // We can just dump the whole bytes array into the blob!
            audioBytes = bytes;
        }

        // Convert to a blob URI directly, avoiding `btoa()` memory crashes and slow UI string concatenation loops
        const blob = new Blob([audioBytes], { type: 'audio/mpeg' });
        return URL.createObjectURL(blob);
    } catch (e) {
        console.error("Failed to parse audio hex:", e);
        return null;
    }
};
