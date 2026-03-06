import express from 'express';
import dns from 'dns/promises';
import net from 'net';
import { pool } from './db';
import { authMiddleware } from './auth';
import axios from 'axios';

const router = express.Router();

const MAX_PROXY_REDIRECTS = 3;

const normalizeHostname = (hostname: string) => hostname.replace(/\.$/, '').toLowerCase();

const isBlockedHostname = (hostname: string) => {
    const normalizedHostname = normalizeHostname(hostname);
    return normalizedHostname === 'localhost' || normalizedHostname.endsWith('.localhost');
};

const isPrivateIpv4 = (ip: string) => {
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

const isPrivateIpv6 = (ip: string) => {
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

const isPrivateAddress = (address: string) => {
    const ipVersion = net.isIP(address);
    if (ipVersion === 4) {
        return isPrivateIpv4(address);
    }
    if (ipVersion === 6) {
        return isPrivateIpv6(address);
    }
    return true;
};

const validateProxyUrl = async (rawUrl: string) => {
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

const fetchProxiedMedia = async (targetUrl: string, redirectCount = 0): Promise<any> => {
    const response = await axios({
        method: 'GET',
        url: targetUrl,
        responseType: 'stream',
        timeout: 15000,
        maxRedirects: 0,
        validateStatus: (status) => status >= 200 && status < 400,
        headers: {
            'Referer': 'https://x.com/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': '*/*'
        }
    });

    if (response.status >= 300 && response.status < 400) {
        if (redirectCount >= MAX_PROXY_REDIRECTS) {
            throw new Error('Too many proxy redirects');
        }

        const redirectLocation = response.headers.location;
        if (!redirectLocation) {
            throw new Error('Redirect missing location header');
        }

        const redirectedUrl = new URL(redirectLocation, targetUrl).toString();
        const validatedRedirectUrl = await validateProxyUrl(redirectedUrl);
        if (!validatedRedirectUrl) {
            throw new Error('Blocked proxy redirect target');
        }

        response.data.destroy();
        return fetchProxiedMedia(validatedRedirectUrl, redirectCount + 1);
    }

    return response;
};

// Get paginated logs
router.get('/logs', authMiddleware, async (req, res) => {
    let limit = parseInt(req.query.limit as string) || 50;
    if (limit > 100) limit = 100;

    let offset = parseInt(req.query.offset as string) || 0;
    if (offset < 0) offset = 0;

    try {
        const query = `
            SELECT id, timestamp, service_name, method, endpoint_url, 
                   response_status, cost, discord_user_id, discord_channel_id,
                   request_body as request_body_snippet, 
                   response_body as response_body_snippet,
                   curl_command
            FROM api_request_logs
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        `;
        const logs = await pool.query(query, [limit, offset]);

        const countQuery = `SELECT COUNT(*) as total FROM api_request_logs`;
        const countResult = await pool.query(countQuery);
        const total = Number(countResult[0].total);

        res.json({ logs, total });
    } catch (error) {
        console.error('Error fetching logs:', error);
        res.status(500).json({ error: 'Internal Server Error' });
    }
});

// Get a single log with full details
router.get('/logs/:id', authMiddleware, async (req, res) => {
    const { id } = req.params;
    try {
        const query = `
            SELECT id, timestamp, service_name, method, endpoint_url, 
                   request_headers, request_body, response_status, 
                   response_headers, response_body, cost, 
                   discord_user_id, discord_channel_id, curl_command
            FROM api_request_logs
            WHERE id = ?
        `;
        const rows = await pool.query(query, [id]);
        if (rows.length === 0) {
            return res.status(404).json({ error: 'Log not found' });
        }
        res.json(rows[0]);
    } catch (error) {
        console.error(`Error fetching log ${id}:`, error);
        res.status(500).json({ error: 'Internal Server Error' });
    }
});

// Proxy media endpoint to bypass CORS and stream remote media for authenticated users.
router.get('/proxy-media', authMiddleware, async (req, res) => {
    const mediaUrl = req.query.url as string;

    if (!mediaUrl) {
        return res.status(400).json({ error: 'Missing media URL' });
    }

    const validatedMediaUrl = await validateProxyUrl(mediaUrl);
    if (!validatedMediaUrl) {
        return res.status(400).json({ error: 'Invalid or disallowed media URL' });
    }

    try {
        const response = await fetchProxiedMedia(validatedMediaUrl);

        // Forward content-type and length headers
        res.setHeader('Content-Type', response.headers['content-type'] || 'application/octet-stream');
        if (response.headers['content-length']) {
            res.setHeader('Content-Length', response.headers['content-length']);
        }
        res.setHeader('Accept-Ranges', 'bytes');
        res.setHeader('Cache-Control', 'private, max-age=3600');
        res.setHeader('Vary', 'Authorization');

        response.data.pipe(res);

    } catch (error: any) {
        console.error('Error proxying media:', error.message);
        const status = error.response?.status || (error.message?.includes('Blocked proxy') ? 403 : 500);
        res.status(status).json({ error: 'Failed to proxy media' });
    }
});

export default router;
