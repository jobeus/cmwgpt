import dns from 'dns/promises';
import net from 'net';

export const MAX_PROXY_REDIRECTS = 3;

export const normalizeHostname = (hostname: string) => hostname.replace(/\.$/, '').toLowerCase();

export const isBlockedHostname = (hostname: string) => {
    const normalizedHostname = normalizeHostname(hostname);
    return normalizedHostname === 'localhost' || normalizedHostname.endsWith('.localhost');
};

export const isPrivateIpv4 = (ip: string) => {
    const octets = ip.split('.').map(Number);
    if (octets.length !== 4 || octets.some((octet) => Number.isNaN(octet) || octet < 0 || octet > 255)) {
        return true;
    }

    const [first, second] = octets;
    if (first === 0 || first === 10 || first === 127) return true;
    if (first === 169 && second === 254) return true;
    if (first === 172 && second >= 16 && second <= 31) return true;
    if (first === 192 && second === 168) return true;
    if (first === 100 && second >= 64 && second <= 127) return true;
    if (first === 198 && (second === 18 || second === 19)) return true;
    return false;
};

export const isPrivateIpv6 = (ip: string) => {
    const normalizedIp = ip.toLowerCase();
    if (normalizedIp === '::' || normalizedIp === '::1') {
        return true;
    }

    if (normalizedIp.startsWith('::ffff:')) {
        const mappedIpv4 = normalizedIp.substring('::ffff:'.length);
        return net.isIP(mappedIpv4) === 4 ? isPrivateIpv4(mappedIpv4) : true;
    }

    return normalizedIp.startsWith('fc')
        || normalizedIp.startsWith('fd')
        || /^fe[89ab]/.test(normalizedIp);
};

export const isPrivateAddress = (address: string) => {
    const ipVersion = net.isIP(address);
    if (ipVersion === 4) {
        return isPrivateIpv4(address);
    }
    if (ipVersion === 6) {
        return isPrivateIpv6(address);
    }
    return true;
};

export const validateProxyUrl = async (rawUrl: string) => {
    try {
        const parsedUrl = new URL(rawUrl);
        if (!['http:', 'https:'].includes(parsedUrl.protocol)) {
            return null;
        }
        if (parsedUrl.username || parsedUrl.password) {
            return null;
        }

        if (isBlockedHostname(parsedUrl.hostname)) {
            return null;
        }

        if (net.isIP(parsedUrl.hostname)) {
            return isPrivateAddress(parsedUrl.hostname) ? null : parsedUrl.toString();
        }

        try {
            const resolvedAddresses = await dns.lookup(parsedUrl.hostname, { all: true, verbatim: true });
            if (resolvedAddresses.some(({ address }) => isPrivateAddress(address))) {
                return null;
            }
        } catch {
            // Let the outbound request fail normally if DNS is temporarily unavailable.
        }

        return parsedUrl.toString();
    } catch {
        return null;
    }
};