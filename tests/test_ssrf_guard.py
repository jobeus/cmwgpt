"""Tests for the SSRF guard helpers."""

import socket
import unittest
from unittest.mock import patch

from src.utils import ssrf_guard


def addrinfo(ip: str):
    """Build a minimal getaddrinfo-style result for an IP."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


class TestAssertPublicUrlSync(unittest.TestCase):
    def test_blocks_non_public_literal_addresses(self):
        for url in [
            "http://127.0.0.1/x",
            "http://10.0.0.5/",
            "https://192.168.1.1/admin",
            "http://172.16.3.4/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
            "http://0.0.0.0/",
            "http://224.0.0.1/",
        ]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                ssrf_guard.assert_public_url_sync(url)

    def test_allows_public_literal_address(self):
        ssrf_guard.assert_public_url_sync("http://93.184.216.34/page")

    def test_blocks_hostname_resolving_to_private_address(self):
        with patch("src.utils.ssrf_guard.socket.getaddrinfo", return_value=addrinfo("10.1.2.3")):
            with self.assertRaises(ValueError):
                ssrf_guard.assert_public_url_sync("https://evil.example.com/payload")

    def test_blocks_hostname_when_any_resolved_address_is_private(self):
        mixed = addrinfo("93.184.216.34") + addrinfo("192.168.0.10")
        with patch("src.utils.ssrf_guard.socket.getaddrinfo", return_value=mixed):
            with self.assertRaises(ValueError):
                ssrf_guard.assert_public_url_sync("https://sneaky.example.com/")

    def test_allows_hostname_resolving_to_public_address(self):
        with patch("src.utils.ssrf_guard.socket.getaddrinfo", return_value=addrinfo("93.184.216.34")):
            ssrf_guard.assert_public_url_sync("https://example.com/page")

    def test_dns_resolution_failure_does_not_block(self):
        with patch("src.utils.ssrf_guard.socket.getaddrinfo", side_effect=socket.gaierror("nxdomain")):
            ssrf_guard.assert_public_url_sync("https://does-not-resolve.invalid/")

    def test_rejects_urls_without_host(self):
        with self.assertRaises(ValueError):
            ssrf_guard.assert_public_url_sync("http:///just-a-path")


class TestAssertPublicUrlAsync(unittest.IsolatedAsyncioTestCase):
    async def test_blocks_loopback(self):
        with self.assertRaises(ValueError):
            await ssrf_guard.assert_public_url("http://127.0.0.1/")

    async def test_allows_public_address(self):
        with patch("src.utils.ssrf_guard.socket.getaddrinfo", return_value=addrinfo("93.184.216.34")):
            await ssrf_guard.assert_public_url("https://example.com/page")
