import express from 'express';
import http from 'http';
import https from 'https';
import net from 'net';
import { pool } from './db';
import { authMiddleware } from './auth';
import axios from 'axios';
import { MAX_PROXY_REDIRECTS, validateProxyUrl, type ValidatedProxyTarget } from './proxySecurity';

const router = express.Router();

// Pin outbound connections to the address vetted during validation so a DNS
// rebinding attacker can't pass validation and then resolve elsewhere.
const createPinnedLookup = (target: ValidatedProxyTarget): net.LookupFunction =>
    ((hostname: string, options: any, callback: any) => {
        if (typeof options === 'function') {
            callback = options;
            options = {};
        }
        if (options?.all) {
            callback(null, [{ address: target.address, family: target.family }]);
        } else {
            callback(null, target.address, target.family);
        }
    }) as net.LookupFunction;

const fetchProxiedMedia = async (target: ValidatedProxyTarget, redirectCount = 0): Promise<any> => {
    const lookup = createPinnedLookup(target);
    const response = await axios({
        method: 'GET',
        url: target.url,
        responseType: 'stream',
        timeout: 15000,
        maxRedirects: 0,
        httpAgent: new http.Agent({ lookup }),
        httpsAgent: new https.Agent({ lookup }),
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

        const redirectedUrl = new URL(redirectLocation, target.url).toString();
        const validatedRedirectTarget = await validateProxyUrl(redirectedUrl);
        if (!validatedRedirectTarget) {
            throw new Error('Blocked proxy redirect target');
        }

        response.data.destroy();
        return fetchProxiedMedia(validatedRedirectTarget, redirectCount + 1);
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
                   LEFT(request_body, 1000) as request_body_snippet,
                   LEFT(response_body, 1000) as response_body_snippet,
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

    const validatedTarget = await validateProxyUrl(mediaUrl);
    if (!validatedTarget) {
        return res.status(400).json({ error: 'Invalid or disallowed media URL' });
    }

    try {
        const response = await fetchProxiedMedia(validatedTarget);

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
