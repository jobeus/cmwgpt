import express from 'express';
import http from 'http';
import { Server } from 'socket.io';
import cors from 'cors';
import authRouter, { socketAuthMiddleware } from './auth';
import { allowedOrigins } from './config';
import logsRouter from './routes';
import { pool } from './db';

const app = express();
const server = http.createServer(app);
const corsOptions = {
    origin: allowedOrigins,
    methods: ['GET', 'POST', 'OPTIONS'],
    allowedHeaders: ['Authorization', 'Content-Type']
};

const io = new Server(server, {
    cors: corsOptions
});

app.use(cors(corsOptions));
app.use(express.json());

// Routes
app.use('/api', authRouter);
app.use('/api', logsRouter);

// Socket.io Middlewares
io.use(socketAuthMiddleware);

const MAX_TIMEOUT_MS = 2 ** 31 - 1;

io.on('connection', (socket) => {
    console.log(`Socket connected: ${socket.id}`);

    // Enforce JWT expiry beyond the handshake: disconnect when the token's exp passes.
    let expiryTimer: NodeJS.Timeout | undefined;
    const exp = (socket as any).user?.exp;
    if (typeof exp === 'number') {
        const msUntilExpiry = exp * 1000 - Date.now();
        if (msUntilExpiry <= 0) {
            socket.disconnect(true);
            return;
        }
        expiryTimer = setTimeout(() => {
            console.log(`Socket token expired, disconnecting: ${socket.id}`);
            socket.disconnect(true);
        }, Math.min(msUntilExpiry, MAX_TIMEOUT_MS));
    }

    socket.on('disconnect', () => {
        if (expiryTimer) clearTimeout(expiryTimer);
        console.log(`Socket disconnected: ${socket.id}`);
    });
});

// Polling for new logs
let lastCheckedId = 0;
const pollNewLogs = async () => {
    try {
        if (lastCheckedId === 0) {
            // First run, find the max ID to start tailing
            const result = await pool.query('SELECT MAX(id) as maxId FROM api_request_logs');
            lastCheckedId = Number(result[0].maxId) || 0;
            return;
        }

        const query = `
            SELECT id, timestamp, service_name, method, endpoint_url, 
                   response_status, cost, discord_user_id, discord_channel_id,
                   LEFT(request_body, 1000) as request_body_snippet, 
                   LEFT(response_body, 1000) as response_body_snippet,
                   curl_command
            FROM api_request_logs
            WHERE id > ?
            ORDER BY timestamp ASC
        `;
        const newLogs = await pool.query(query, [lastCheckedId]);

        if (newLogs.length > 0) {
            // Advance the cursor before emitting so a slow emit can't cause re-reads.
            newLogs.forEach((log: any) => {
                lastCheckedId = Math.max(lastCheckedId, Number(log.id));
            });
            newLogs.forEach((log: any) => {
                io.emit('new-log', log);
            });
        }
    } catch (err) {
        console.error('Error polling for new logs:', err);
    } finally {
        // Self-schedule so slow queries can't overlap the next poll.
        setTimeout(pollNewLogs, POLL_INTERVAL_MS);
    }
};

// Poll every 1.5 seconds
const POLL_INTERVAL_MS = 1500;
setTimeout(pollNewLogs, POLL_INTERVAL_MS);

const PORT = Number(process.env.PORT || 3001);
const HOST = process.env.LOG_VIEWER_HOST || '127.0.0.1';

server.listen(PORT, HOST, () => {
    console.log(`Log viewer backend listening on ${HOST}:${PORT}`);
});
