import express from 'express';
import { pool } from './db';
import { authMiddleware } from './auth';
import axios from 'axios';

const router = express.Router();

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

// Proxy media endpoint to bypass CORS and stream Twitter MP4s, images, etc.
router.get('/proxy-media', async (req, res) => {
    const mediaUrl = req.query.url as string;

    if (!mediaUrl) {
        return res.status(400).json({ error: 'Missing media URL' });
    }

    try {
        const response = await axios({
            method: 'GET',
            url: mediaUrl,
            responseType: 'stream',
            headers: {
                'Referer': 'https://x.com/',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': '*/*'
            }
        });

        // Forward content-type and length headers
        res.setHeader('Content-Type', response.headers['content-type'] || 'application/octet-stream');
        if (response.headers['content-length']) {
            res.setHeader('Content-Length', response.headers['content-length']);
        }
        res.setHeader('Accept-Ranges', 'bytes');
        res.setHeader('Cache-Control', 'public, max-age=86400'); // cache proxied media for 24h

        response.data.pipe(res);

    } catch (error: any) {
        console.error('Error proxying media:', error.message);
        res.status(500).json({ error: 'Failed to proxy media' });
    }
});

export default router;
