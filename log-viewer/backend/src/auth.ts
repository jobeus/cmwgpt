import express, { Request, Response, NextFunction } from 'express';
import rateLimit from 'express-rate-limit';
import jwt, { type SignOptions } from 'jsonwebtoken';
import pam from 'authenticate-pam';
import { JWT_EXPIRES_IN, JWT_SECRET } from './config';

const router = express.Router();

const loginRateLimiter = rateLimit({
    windowMs: 60 * 1000,
    limit: 5,
    standardHeaders: true,
    legacyHeaders: false,
    message: { error: 'Too many login attempts, please try again later' }
});

const buildToken = (username: string) => jwt.sign(
    { username },
    JWT_SECRET,
    { expiresIn: JWT_EXPIRES_IN as SignOptions['expiresIn'] }
);

export const isDevelopmentAuthEnabled = (env: NodeJS.ProcessEnv = process.env) =>
    env.LOG_VIEWER_DEV_AUTH_ENABLED?.trim().toLowerCase() === 'true';

export const hasDevelopmentAuthCredentials = (env: NodeJS.ProcessEnv = process.env) =>
    Boolean(env.LOG_VIEWER_DEV_USERNAME?.trim() && env.LOG_VIEWER_DEV_PASSWORD);

export const matchesDevelopmentCredentials = (
    username: string,
    password: string,
    env: NodeJS.ProcessEnv = process.env
) => {
    const expectedUsername = env.LOG_VIEWER_DEV_USERNAME?.trim();
    const expectedPassword = env.LOG_VIEWER_DEV_PASSWORD;

    return Boolean(
        isDevelopmentAuthEnabled(env)
        && expectedUsername
        && expectedPassword
        && username === expectedUsername
        && password === expectedPassword
    );
};

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
    } catch {
        return res.status(401).json({ error: 'Invalid token' });
    }
};

router.post('/login', loginRateLimiter, (req: Request, res: Response) => {
    const { username, password } = req.body;

    if (!username || !password) {
        return res.status(400).json({ error: 'Username and password required' });
    }

    if (isDevelopmentAuthEnabled()) {
        if (!hasDevelopmentAuthCredentials()) {
            console.error('LOG_VIEWER_DEV_AUTH_ENABLED is true but development credentials are not configured');
            return res.status(500).json({ error: 'Development auth is misconfigured' });
        }

        if (!matchesDevelopmentCredentials(username, password)) {
            return res.status(401).json({ error: 'Invalid credentials' });
        }

        console.log(`Development auth successful for ${username}`);
        const token = buildToken(username);
        return res.json({ token, username });
    }

    pam.authenticate(username, password, (err: any) => {
        if (err) {
            console.error(`PAM Auth failed for ${username}: ${err}`);
            return res.status(401).json({ error: 'Invalid credentials' });
        }

        console.log(`PAM Auth successful for ${username}`);
        const token = buildToken(username);
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
    } catch {
        next(new Error('Authentication error'));
    }
};

export default router;
