import express, { Request, Response, NextFunction } from 'express';
import jwt from 'jsonwebtoken';
import pam from 'authenticate-pam';
import fs from 'fs';
import path from 'path';

const router = express.Router();

const JWT_SECRET = process.env.JWT_SECRET || 'fallback-secret-for-dev-only-change-in-prod';

// Attempt to load the user map. This gives us a predefined list of valid IDs, but PAM doesn't care.
// For the UI we might want to know who is logged in, but for PAM auth we just rely on OS.
export const authMiddleware = (req: Request, res: Response, next: NextFunction) => {
    const authHeader = req.headers.authorization;
    if (!authHeader) {
        return res.status(401).json({ error: 'Missing authorization header' });
    }

    const token = authHeader.split(' ')[1];
    if (!token) {
        return res.status(401).json({ error: 'Missing token' });
    }

    try {
        const decoded = jwt.verify(token, JWT_SECRET);
        (req as any).user = decoded;
        next();
    } catch (err) {
        return res.status(401).json({ error: 'Invalid token' });
    }
};

router.post('/login', (req: Request, res: Response) => {
    const { username, password } = req.body;

    if (!username || !password) {
        return res.status(400).json({ error: 'Username and password required' });
    }

    pam.authenticate(username, password, (err: any) => {
        if (err) {
            console.error(`PAM Auth failed for ${username}: ${err}`);
            return res.status(401).json({ error: 'Invalid credentials' });
        }

        console.log(`PAM Auth successful for ${username}`);
        const token = jwt.sign({ username }, JWT_SECRET, { expiresIn: '7d' });
        res.json({ token, username });
    });
});

// Middleware for Socket.io
export const socketAuthMiddleware = (socket: any, next: (err?: Error) => void) => {
    const token = socket.handshake.auth?.token;
    if (!token) {
        return next(new Error('Authentication error'));
    }
    try {
        const decoded = jwt.verify(token, JWT_SECRET);
        socket.user = decoded;
        next();
    } catch (err) {
        next(new Error('Authentication error'));
    }
};

export default router;
