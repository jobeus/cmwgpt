import dotenv from 'dotenv';
import fs from 'fs';
import path from 'path';

const repoRoot = path.resolve(__dirname, '../../../');

const resolveCandidatePath = (candidate: string) => (
    path.isAbsolute(candidate) ? candidate : path.resolve(repoRoot, candidate)
);

export const resolveEnvFilePath = (env: NodeJS.ProcessEnv = process.env) => {
    const explicitPath = env.CMWGPT_ENV_FILE?.trim();
    if (explicitPath) {
        return resolveCandidatePath(explicitPath);
    }

    for (const candidate of ['.env.production', '.env']) {
        const resolvedPath = resolveCandidatePath(candidate);
        if (fs.existsSync(resolvedPath)) {
            return resolvedPath;
        }
    }

    return undefined;
};

export const loadResolvedEnvFile = (env: NodeJS.ProcessEnv = process.env) => {
    const envFilePath = resolveEnvFilePath(env);
    if (envFilePath) {
        dotenv.config({ path: envFilePath });
    }
    return envFilePath;
};