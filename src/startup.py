"""Application startup, service composition, and shutdown."""

import logging
from dataclasses import dataclass
from typing import Any, Optional

from src.bot.handlers.mention import MentionHandler
from src.config import AppConfig, get_system_prompt, load_config
from src.db.connection import close_db_pool
from src.db.memory_store import MemoryStore
from src.services.announcement_service import AnnouncementService
from src.services.auto_update_service import AutoUpdateService
from src.services.death_service import DeathService
from src.services.message_service import MessageService
from src.services.openai_service import OpenAIService
from src.services.anthropic_service import AnthropicService
from src.services.gemini_service import GeminiService
from src.services.memory_service import MemoryService
from src.services.paste_service import PasteService
from src.services.queue_service import QueueService
from src.services.restart_handler import RestartHandler
from src.services.runpod_service import RunpodService
from src.services.state_service import StateService
from src.utils.cache_utils import flush_all_caches
from src.utils.url_utils import inject_article_cache

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppServices:
    state_service: Any
    queue_service: Any
    openai_service: Any
    gemini_service: Any
    message_service: Any
    paste_service: Any
    runpod_service: Any
    restart_handler: RestartHandler
    auto_update_service: AutoUpdateService
    announcement_service: AnnouncementService
    death_service: DeathService
    mention_handler: MentionHandler
    anthropic_service: Any = None
    memory_store: Any = None
    memory_service: Any = None


def create_services(config: Optional[AppConfig] = None) -> AppServices:
    app_config = config or load_config()

    state_service = StateService()
    queue_service = QueueService()
    openai_service = OpenAIService()
    gemini_service = GeminiService(api_key=app_config.gemini_api_key)
    anthropic_service = AnthropicService(api_key=app_config.anthropic_api_key)
    paste_service = PasteService(cache_injector=inject_article_cache)
    message_service = MessageService(paste_service_instance=paste_service)
    runpod_service = RunpodService()

    restart_handler = RestartHandler(state_service=state_service)
    auto_update_service = AutoUpdateService(
        queue_service=queue_service,
        secret=app_config.deploy_webhook_secret,
        host=app_config.deploy_webhook_host,
        port=app_config.deploy_webhook_port,
    )
    announcement_service = AnnouncementService(
        state_service=state_service,
        paste_service=paste_service,
        quiet_updates=app_config.quiet_updates,
    )
    death_service = DeathService(
        state_service=state_service,
        death_channel_id=app_config.death_channel_id,
        openai_service=openai_service,
        gemini_service=gemini_service,
        anthropic_service=anthropic_service,
        model=app_config.default_model,
    )
    memory_store = MemoryStore()
    memory_service = MemoryService(
        store=memory_store,
        config=app_config,
        openai_service=openai_service,
        gemini_service=gemini_service,
        anthropic_service=anthropic_service,
    )
    mention_handler = MentionHandler(
        state_service=state_service,
        openai_service=openai_service,
        gemini_service=gemini_service,
        anthropic_service=anthropic_service,
        message_service=message_service,
        queue_service=queue_service,
        system_prompt_loader=lambda: get_system_prompt(app_config.system_prompt_path),
        include_num_chatlines=app_config.include_num_chatlines,
        user_notes_provider=memory_service.get_notes_block,
    )

    return AppServices(
        state_service=state_service,
        queue_service=queue_service,
        openai_service=openai_service,
        gemini_service=gemini_service,
        message_service=message_service,
        paste_service=paste_service,
        runpod_service=runpod_service,
        restart_handler=restart_handler,
        auto_update_service=auto_update_service,
        announcement_service=announcement_service,
        death_service=death_service,
        mention_handler=mention_handler,
        anthropic_service=anthropic_service,
        memory_store=memory_store,
        memory_service=memory_service,
    )


async def shutdown_services(services: AppServices) -> None:
    """Stop background jobs, let in-flight replies finish, then close every client.

    Each step is independent: a failure is logged and the rest still run.
    """
    async def step(name: str, action) -> None:
        try:
            result = action()
            if hasattr(result, "__await__"):
                await result
            logger.info(f"Shutdown: {name} done")
        except Exception as e:
            logger.error(f"Shutdown: {name} failed: {e}")

    await step("deploy webhook stop", services.auto_update_service.stop)
    if services.memory_service is not None:
        await step("memory job stop", services.memory_service.stop)
    await step("death service close", services.death_service.close)
    # Lets each channel finish the reply it is working on (bounded by a timeout).
    await step("queue drain", services.queue_service.stop)
    await step("openai close", services.openai_service.close)
    await step("gemini close", services.gemini_service.close)
    if services.anthropic_service is not None:
        await step("anthropic close", services.anthropic_service.close)
    await step("runpod close", services.runpod_service.close)
    await step("cache flush", flush_all_caches)
    await step("db pool close", close_db_pool)


def create_bot_client(config: Optional[AppConfig] = None):
    from src.bot.client import DiscordBotClient

    app_config = config or load_config()
    services = create_services(app_config)
    return DiscordBotClient(
        config=app_config,
        services=services,
        mention_handler=services.mention_handler,
    )
