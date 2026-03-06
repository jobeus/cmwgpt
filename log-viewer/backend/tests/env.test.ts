import dotenv from 'dotenv';
import fs from 'fs';
import path from 'path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { loadResolvedEnvFile, resolveEnvFilePath } from '../src/env';

const repoRoot = path.resolve(__dirname, '../../../');

describe('env file resolution', () => {
    afterEach(() => {
        vi.restoreAllMocks();
    });

    it('prefers an explicit CMWGPT_ENV_FILE', () => {
        const result = resolveEnvFilePath({ CMWGPT_ENV_FILE: '.env.production' });

        expect(result).toBe(path.resolve(repoRoot, '.env.production'));
    });

    it('falls back to .env.production before .env', () => {
        vi.spyOn(fs, 'existsSync').mockImplementation((candidatePath) => (
            candidatePath === path.resolve(repoRoot, '.env.production')
        ));

        const result = resolveEnvFilePath({});

        expect(result).toBe(path.resolve(repoRoot, '.env.production'));
    });

    it('loads the resolved env file with dotenv', () => {
        vi.spyOn(fs, 'existsSync').mockImplementation((candidatePath) => (
            candidatePath === path.resolve(repoRoot, '.env')
        ));
        const configSpy = vi.spyOn(dotenv, 'config').mockReturnValue({ parsed: {} });

        const result = loadResolvedEnvFile({});

        expect(result).toBe(path.resolve(repoRoot, '.env'));
        expect(configSpy).toHaveBeenCalledWith({ path: path.resolve(repoRoot, '.env') });
    });
});