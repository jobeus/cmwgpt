"""Application startup and service composition."""

from dataclasses import dataclass
from typing import Any, Optional

from src.bot.handlers.mention import MentionHandler
from src.config import AppConfig, get_system_prompt, load_config
from src.services.announcement_service import AnnouncementService
from src.services.auto_update_service import AutoUpdateService
from src.services.death_service import DeathService
from src.services.message_service import MessageService
from src.services.openai_service import OpenAIService
from src.services.gemini_service import GeminiService
from src.services.paste_service import PasteService
from src.services.queue_service import QueueService
from src.services.restart_handler import RestartHandler
from src.services.runpod_service import RunpodService
from src.services.state_service import StateService
from src.utils.url_utils import inject_article_cache


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


def create_services(config: Optional[AppConfig] = None) -> AppServices:
    app_config = config or load_config()

    state_service = StateService()
    queue_service = QueueService()
    openai_service = OpenAIService()
    gemini_service = GeminiService(api_key=app_config.gemini_api_key)
    paste_service = PasteService(cache_injector=inject_article_cache)
    message_service = MessageService(paste_service_instance=paste_service)
    runpod_service = RunpodService()

    restart_handler = RestartHandler(state_service=state_service)
    auto_update_service = AutoUpdateService(
        queue_service=queue_service,
        enabled=app_config.keep_up_to_date_with_git,
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
        model=app_config.default_model,
    )
    mention_handler = MentionHandler(
        state_service=state_service,
        openai_service=openai_service,
        gemini_service=gemini_service,
        message_service=message_service,
        queue_service=queue_service,
        system_prompt_loader=lambda: get_system_prompt(app_config.system_prompt_path),
        include_num_chatlines=app_config.include_num_chatlines,
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
    )


def create_bot_client(config: Optional[AppConfig] = None):
    from src.bot.client import DiscordBotClient

    app_config = config or load_config()
    services = create_services(app_config)
    return DiscordBotClient(
        config=app_config,
        services=services,
        mention_handler=services.mention_handler,
    )
