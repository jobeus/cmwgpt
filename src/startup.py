"""Application startup, service composition, and shutdown."""

import logging
from dataclasses import dataclass
from typing import Any, Optional

from src.bot.handlers.mention import MentionHandler
from src.config import AppConfig, get_system_prompt, load_config
from src.db.memory_store import MemoryStore
from src.db.redis_client import close_redis, get_sync_redis
from src.db.state_store import StateStore
from src.services.announcement_service import AnnouncementService
from src.services.auto_update_service import AutoUpdateService
from src.services.cost_service import CostStore, set_cost_store
from src.services.death_service import DeathService
from src.services.message_service import MessageService
from src.services.anthropic_service import AnthropicService
from src.services.gemini_service import GeminiService
from src.services.irc_log_service import load_irc_log
from src.services.quote_service import load_quote_file
from src.services.memory_service import MemoryService
from src.services.package_update_service import PackageUpdateService
from src.services.paste_service import PasteService
from src.services.queue_service import QueueService
from src.services.reminder_service import ReminderService
from src.services.timezone_service import TimezoneStore
from src.services.video_service import VideoWatcher
from src.telemetry import set_image_uploader
from src.services.restart_handler import RestartHandler
from src.services.state_service import StateService
from src.utils.url_utils import inject_article_cache

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppServices:
    state_service: Any
    queue_service: Any
    gemini_service: Any
    anthropic_service: Any
    message_service: Any
    paste_service: Any
    restart_handler: RestartHandler
    auto_update_service: AutoUpdateService
    announcement_service: AnnouncementService
    death_service: DeathService
    mention_handler: MentionHandler
    memory_store: Any = None
    memory_service: Any = None
    cost_store: Any = None
    reminder_service: Any = None
    package_update_service: Any = None


def create_services(config: Optional[AppConfig] = None) -> AppServices:
    app_config = config or load_config()

    # Every paid call reports its cost here, for /cost. Under tests there's no Redis.
    cost_store = None if app_config.is_testing else CostStore()
    set_cost_store(cost_store)

    # Settings, restart state and each channel's conversation state. Under tests there's
    # no Redis, so it lives only in memory.
    state_store = StateStore(None if app_config.is_testing else get_sync_redis)
    state_service = StateService(state_store)
    queue_service = QueueService()
    gemini_service = GeminiService(api_key=app_config.gemini_api_key, context_store=state_store)
    anthropic_service = AnthropicService(
        api_key=app_config.anthropic_api_key,
        context_store=state_store,
        logfire_read_token="" if app_config.is_testing else app_config.logfire_read_token,
        logfire_mcp_url=app_config.logfire_mcp_url,
        logs_channel_id=app_config.logs_channel_id,
    )
    paste_service = PasteService(cache_injector=inject_article_cache)
    # Links to the images in the traces; only used while tracing is on.
    set_image_uploader(paste_service.upload_image)
    message_service = MessageService(paste_service=paste_service)

    restart_handler = RestartHandler(state_service=state_service)
    auto_update_service = AutoUpdateService(
        queue_service=queue_service,
        secret=app_config.deploy_webhook_secret,
        host=app_config.deploy_webhook_host,
        port=app_config.deploy_webhook_port,
    )
    package_update_service = PackageUpdateService(
        store=state_store,
        restart=auto_update_service.trigger_restart,
        interval_hours=app_config.package_update_check_hours,
        is_testing=app_config.is_testing,
    )
    announcement_service = AnnouncementService(
        state_service=state_service,
        paste_service=paste_service,
        quiet_updates=app_config.quiet_updates,
    )
    death_service = DeathService(
        state_service=state_service,
        store=state_store,
        death_channel_id=app_config.death_channel_id,
        gemini_service=gemini_service,
        anthropic_service=anthropic_service,
        model=app_config.default_model,
    )
    memory_store = MemoryStore()
    memory_service = MemoryService(
        store=memory_store,
        config=app_config,
        gemini_service=gemini_service,
        anthropic_service=anthropic_service,
    )
    reminder_service = ReminderService(is_testing=app_config.is_testing)
    video_watcher = VideoWatcher(gemini_service) if app_config.gemini_api_key else None
    irc_log = load_irc_log(app_config.irc_log_path, app_config.irc_nicks_path)
    mention_handler = MentionHandler(
        state_service=state_service,
        gemini_service=gemini_service,
        anthropic_service=anthropic_service,
        message_service=message_service,
        queue_service=queue_service,
        system_prompt_loader=lambda: get_system_prompt(app_config.system_prompt_path),
        include_num_chatlines=app_config.include_num_chatlines,
        user_notes_provider=memory_service.get_notes_block,
        default_image_model=app_config.default_image_model,
        irc_log=irc_log,
        memory_corrector=memory_service.correct if memory_service.available else None,
        wiki_views=death_service.get_avg_monthly_views,
        quote_file=load_quote_file(app_config.old_quote_bot_path),
        memory_index_provider=memory_service.get_memory_index,
        self_state_provider=memory_service.get_self_block,
        self_state_updater=memory_service.update_self,
        reminder_service=reminder_service,
        timezone_store=None if app_config.is_testing else TimezoneStore(),
        video_summarizer=video_watcher.summarize_youtube if video_watcher else None,
        video_watcher=video_watcher.watch if video_watcher else None,
        context_store=state_store,
    )

    return AppServices(
        state_service=state_service,
        queue_service=queue_service,
        gemini_service=gemini_service,
        message_service=message_service,
        paste_service=paste_service,
        restart_handler=restart_handler,
        auto_update_service=auto_update_service,
        announcement_service=announcement_service,
        death_service=death_service,
        mention_handler=mention_handler,
        anthropic_service=anthropic_service,
        memory_store=memory_store,
        memory_service=memory_service,
        cost_store=cost_store,
        reminder_service=reminder_service,
        package_update_service=package_update_service,
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
            logger.info("Shutdown: %s done", name)
        except Exception as e:
            logger.error("Shutdown: %s failed: %s", name, e, exc_info=True)

    await step("deploy webhook stop", services.auto_update_service.stop)
    if getattr(services, "package_update_service", None) is not None:
        await step("package update checks stop", services.package_update_service.stop)
    if services.memory_service is not None:
        await step("memory job stop", services.memory_service.stop)
    if getattr(services, "reminder_service", None) is not None:
        await step("reminders stop", services.reminder_service.stop)
    await step("death service close", services.death_service.close)
    # Lets each channel finish the reply it is working on (bounded by a timeout).
    await step("queue drain", services.queue_service.stop)
    await step("gemini close", services.gemini_service.close)
    await step("anthropic close", services.anthropic_service.close)
    await step("redis close", close_redis)


def create_bot_client(config: Optional[AppConfig] = None):
    from src.bot.client import DiscordBotClient

    app_config = config or load_config()
    services = create_services(app_config)
    return DiscordBotClient(
        config=app_config,
        services=services,
    )
