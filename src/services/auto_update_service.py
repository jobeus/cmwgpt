"""
Auto-update service for the Discord bot.

Listens for a signed "deploy" webhook (sent by GitHub Actions once CI passes on
main) and triggers the normal restart flow, which pulls the new code.

Request format (POST /deploy):
    X-Deploy-Timestamp: <unix seconds>
    X-Deploy-Signature: sha256=<hex HMAC-SHA256 of "<timestamp>.<raw body>" keyed by the secret>
    body: {"sha": "<commit sha>", "ref": "refs/heads/main"}
"""

import hashlib
import hmac
import json
import logging
import time
from typing import Awaitable, Callable, Optional

from aiohttp import web

from src.utils.git_utils import get_current_commit_hash as default_get_current_commit_hash

logger = logging.getLogger(__name__)

# Requests whose timestamp is further than this from now are rejected (replay guard).
MAX_CLOCK_SKEW_SECONDS = 300


def sign_deploy_request(secret: str, timestamp: str, body: bytes) -> str:
    """The X-Deploy-Signature value for a request body sent at ``timestamp``."""
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class AutoUpdateService:
    """Webhook listener that restarts the bot onto newly deployed commits."""

    def __init__(
        self,
        *,
        queue_service,
        secret: str = "",
        host: str = "0.0.0.0",
        port: int = 8787,
        get_current_commit_hash_fn: Callable[[], Optional[str]] = default_get_current_commit_hash,
        clock: Callable[[], float] = time.time,
    ):
        self._queue_service = queue_service
        self._secret = secret
        self._host = host
        self._port = port
        self._get_current_commit_hash = get_current_commit_hash_fn
        self._clock = clock
        self._restart_callback: Optional[Callable[..., Awaitable[None]]] = None
        self._runner: Optional[web.AppRunner] = None

    @property
    def enabled(self) -> bool:
        return bool(self._secret)

    def set_restart_callback(self, callback: Callable[..., Awaitable[None]]) -> None:
        """Set the callback function to call when a restart is needed."""
        self._restart_callback = callback

    def build_app(self) -> web.Application:
        app = web.Application(client_max_size=64 * 1024)
        app.router.add_post("/deploy", self._handle_deploy)
        return app

    async def start(self) -> None:
        """Start the webhook listener if a secret is configured."""
        if not self.enabled:
            logger.info("Deploy webhook disabled (DEPLOY_WEBHOOK_SECRET not set)")
            return
        if self._runner is not None:
            logger.warning("Deploy webhook already running")
            return

        runner = web.AppRunner(self.build_app(), access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner, self._host, self._port).start()
        except OSError as e:
            logger.error(f"Deploy webhook could not bind {self._host}:{self._port}: {e}")
            await runner.cleanup()
            return
        self._runner = runner
        logger.info(f"Deploy webhook listening on {self._host}:{self._port}/deploy")

    async def stop(self) -> None:
        """Stop the webhook listener."""
        if self._runner is None:
            return
        runner, self._runner = self._runner, None
        await runner.cleanup()
        logger.info("Deploy webhook stopped")

    async def trigger_restart(self, manual: bool = False) -> bool:
        """Trigger a restart of the bot."""
        if not self._restart_callback:
            logger.error("No restart callback set")
            return False

        restart_type = "manual" if manual else "automatic"
        logger.info(f"Triggering {restart_type} restart")

        # Create a wrapper that passes the manual flag
        async def restart_wrapper():
            await self._restart_callback(manual=manual)

        # Queue the restart through the queue service
        return await self._queue_service.queue_restart(restart_wrapper)

    def _verify(self, request_timestamp: str, signature: str, body: bytes) -> bool:
        try:
            sent_at = int(request_timestamp)
        except ValueError:
            return False
        if abs(self._clock() - sent_at) > MAX_CLOCK_SKEW_SECONDS:
            return False
        expected = sign_deploy_request(self._secret, request_timestamp, body)
        return hmac.compare_digest(expected, signature)

    async def _handle_deploy(self, request: web.Request) -> web.Response:
        body = await request.read()
        timestamp = request.headers.get("X-Deploy-Timestamp", "")
        signature = request.headers.get("X-Deploy-Signature", "")
        if not self._verify(timestamp, signature, body):
            logger.warning(f"Rejected deploy webhook from {request.remote}: bad signature or timestamp")
            return web.json_response({"error": "unauthorized"}, status=401)

        try:
            sha = str(json.loads(body).get("sha", ""))
        except (ValueError, AttributeError):
            return web.json_response({"error": "invalid body"}, status=400)

        current = self._get_current_commit_hash()
        if sha and current and sha == current:
            logger.info(f"Deploy webhook for {sha[:7]}: already running it")
            return web.json_response({"status": "up-to-date", "sha": current})

        logger.info(f"Deploy webhook for {sha[:7] or '?'} (running {current[:7] if current else '?'}), restarting")
        if not await self.trigger_restart(manual=False):
            return web.json_response({"error": "restart could not be queued"}, status=503)
        return web.json_response({"status": "restarting", "sha": sha}, status=202)

    def get_status(self) -> dict:
        """Get the current status of the auto-update service."""
        return {
            "enabled": self.enabled,
            "running": self._runner is not None,
            "host": self._host,
            "port": self._port,
        }
