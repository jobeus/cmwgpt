import express from 'express';
import { pool } from './db';
import { authMiddleware } from './auth';

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

export default router;
