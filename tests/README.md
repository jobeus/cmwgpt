# Test suite guide

This repository has a Python-heavy test suite that covers bot startup, commands, services, downloader utilities, the chat tools, and Redis-backed memory and caches.

## Fastest useful commands

| Goal | Command |
| --- | --- |
| Default suite | `make test` |
| Verbose suite | `make test-verbose` |
| Coverage run | `make test-coverage` |
| Single module | `make test-specific TEST=tests/test_downloader_utils.py` |
| Direct pytest | `.venv/bin/python -m pytest` |

`make test` runs pytest (configured in `pytest.ini`), which runs both the `unittest.TestCase` classes and the function-style tests. CI and the pre-commit hook use the same command.

## What the suite covers

| Area | Representative modules |
| --- | --- |
| Startup and client wiring | `test_main.py`, `test_startup.py`, `test_bot_client.py` |
| Slash commands | `test_system_commands.py`, `test_image_commands.py`, `test_death_commands.py` |
| Core services | `test_queue_service.py`, `test_state_service.py`, `test_message_service.py`, `test_image_service.py` |
| Background features | `test_death_service.py`, `test_auto_update.py`, `test_package_update_service.py`, `test_reminders.py`, `test_memory_service.py` |
| Model services and tools | `test_anthropic_service.py`, `test_gemini_service.py`, `test_chat_tools.py`, `test_streaming_reply.py` |
| Mention/prompt handling | `test_mention_handler.py`, `test_chat_context.py`, `test_message_utils.py`, `test_discord_helper.py` |
| Downloader/media helpers | `test_downloader_utils.py`, `test_youtube_utils.py`, `test_tiktok_utils.py`, `test_twitter_utils.py`, `test_facebook_utils.py`, `test_url_utils.py`, `test_http_client.py`, `test_ssrf_guard.py` |
| Redis stores and logging | `test_memory_store.py`, `test_state_store.py`, `test_cache_utils.py`, `test_logger.py`, `test_mention_log.py`, `test_telemetry.py` |

## Downloader/provider flow reflected by the tests

| Source | Current behavior under test |
| --- | --- |
| YouTube | `youtube_transcript_api`, optional proxy use, persistent caching |
| TikTok | `yt-dlp` -> ffmpeg -> Groq transcription, with fallback/proxy behavior |
| Twitter/X | RapidAPI context lookup plus optional video-transcription path |
| Facebook | `yt-dlp` -> ffmpeg -> Groq transcription |
| Articles | `fetch_public`, then `trafilatura` (retried with `favor_recall`) |

`test_downloader_utils.py` covers the orchestration layer, while the source-specific modules cover the provider helpers themselves.

## Good focused-test patterns

```bash
.venv/bin/python -m pytest tests/test_mention_handler.py
.venv/bin/python -m pytest tests/test_downloader_utils.py
.venv/bin/python -m pytest tests/test_twitter_utils.py -q
```

## Practical notes

- Some tests patch external integrations rather than performing live network calls.
- If test imports fail, install both runtime and test dependencies first.
- When iterating, prefer the smallest test target that proves your change.
