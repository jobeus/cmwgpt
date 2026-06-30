# Test suite guide

This repository has a Python-heavy test suite that covers bot startup, commands, services, downloader utilities, and DB/logging behavior.

## Fastest useful commands

| Goal | Command |
| --- | --- |
| Default suite | `make test` |
| Verbose suite | `make test-verbose` |
| Coverage run | `make test-coverage` |
| Single module through helper | `make test-specific TEST=downloader_utils` |
| Direct pytest | `.venv/bin/python -m pytest` |
| Direct unittest discovery | `.venv/bin/python -m unittest discover tests -v` |

`make test` uses `tests/run_tests.py`, which discovers `test_*.py` modules and prints a summary.

## What the suite covers

| Area | Representative modules |
| --- | --- |
| Startup and client wiring | `test_main.py`, `test_startup.py`, `test_bot_client.py` |
| Slash commands | `test_system_commands.py`, `test_image_commands.py`, `test_death_commands.py` |
| Core services | `test_queue_service.py`, `test_state_service.py`, `test_message_service.py`, `test_runpod_service.py` |
| Background features | `test_death_service.py`, `test_auto_update.py` |
| Mention/prompt handling | `test_mention_handler.py`, `test_openai_handler.py`, `test_message_utils.py`, `test_discord_helper.py` |
| Downloader/media helpers | `test_downloader_utils.py`, `test_youtube_utils.py`, `test_tiktok_utils.py`, `test_twitter_utils.py`, `test_facebook_utils.py`, `test_url_utils.py` |
| Database/logging | `test_db_connection.py`, `test_db_logger.py` |

## Downloader/provider flow reflected by the tests

| Source | Current behavior under test |
| --- | --- |
| YouTube | `youtube_transcript_api`, optional proxy use, persistent caching |
| TikTok | `yt-dlp` -> ffmpeg -> Groq transcription, with fallback/proxy behavior |
| Twitter/X | RapidAPI context lookup plus optional video-transcription path |
| Facebook | `yt-dlp` -> ffmpeg -> Groq transcription |
| Articles | `httpx` + `trafilatura` + `newspaper3k` fallback |

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
