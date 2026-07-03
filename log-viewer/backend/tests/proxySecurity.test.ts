import dns from 'dns/promises';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
    isBlockedHostname,
    isPrivateIpv4,
    isPrivateIpv6,
    normalizeHostname,
    validateProxyUrl
} from '../src/proxySecurity';

vi.mock('dns/promises', () => ({
    default: {
        lookup: vi.fn()
    }
}));

const mockedLookup = vi.mocked(dns.lookup);

describe('proxySecurity helpers', () => {
    beforeEach(() => {
        mockedLookup.mockReset();
    });

    it('normalizes hostnames and blocks localhost variants', () => {
        expect(normalizeHostname('Example.COM.')).toBe('example.com');
        expect(isBlockedHostname('localhost')).toBe(true);
        expect(isBlockedHostname('api.localhost')).toBe(true);
        expect(isBlockedHostname('example.com')).toBe(false);
    });

    it('detects private IPv4 and IPv6 ranges', () => {
        expect(isPrivateIpv4('10.0.0.5')).toBe(true);
        expect(isPrivateIpv4('192.168.1.10')).toBe(true);
        expect(isPrivateIpv4('93.184.216.34')).toBe(false);
        expect(isPrivateIpv6('::1')).toBe(true);
        expect(isPrivateIpv6('fd00::1')).toBe(true);
        expect(isPrivateIpv6('2001:4860:4860::8888')).toBe(false);
    });

    it('rejects invalid protocols, credentials, and localhost URLs', async () => {
        await expect(validateProxyUrl('ftp://example.com/file.txt')).resolves.toBeNull();
        await expect(validateProxyUrl('https://user:pass@example.com/private')).resolves.toBeNull();
        await expect(validateProxyUrl('https://localhost:3000/media.png')).resolves.toBeNull();
        await expect(validateProxyUrl('https://127.0.0.1/media.png')).resolves.toBeNull();
    });

    it('rejects hostnames that resolve to private IPs', async () => {
        mockedLookup.mockResolvedValue([{ address: '192.168.1.15', family: 4 }] as never);

        await expect(validateProxyUrl('https://example.com/private.png')).resolves.toBeNull();
    });

    it('allows public URLs and pins the resolved address', async () => {
        mockedLookup.mockResolvedValue([{ address: '93.184.216.34', family: 4 }] as never);

        await expect(validateProxyUrl('https://example.com/media.png')).resolves.toEqual({
            url: 'https://example.com/media.png',
            address: '93.184.216.34',
            family: 4
        });
    });

    it('pins literal IP hostnames without a DNS lookup', async () => {
        await expect(validateProxyUrl('https://93.184.216.34/media.png')).resolves.toEqual({
            url: 'https://93.184.216.34/media.png',
            address: '93.184.216.34',
            family: 4
        });
        expect(mockedLookup).not.toHaveBeenCalled();
    });

    it('rejects URLs when DNS lookup fails', async () => {
        mockedLookup.mockRejectedValue(new Error('temporary dns failure'));

        await expect(validateProxyUrl('https://example.com/media.png')).resolves.toBeNull();
    });

    it('rejects URLs when DNS returns no addresses', async () => {
        mockedLookup.mockResolvedValue([] as never);

        await expect(validateProxyUrl('https://example.com/media.png')).resolves.toBeNull();
    });
});