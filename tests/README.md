# Test suite overview

This repository includes a Python test suite for the bot, services, utilities, and downloader pipeline.

## Main ways to run tests

### Make targets

```bash
make test
make test-verbose
make test-coverage
make test-specific TEST=config
```

### Direct commands

```bash
.venv/bin/python tests/run_tests.py
.venv/bin/python -m unittest discover tests -v
.venv/bin/python -m pytest
```

`make test` uses `tests/run_tests.py`, which discovers `test_*.py` modules and prints a summary.

## What is covered

Representative test modules currently include:

- startup/client wiring
  - `test_main.py`
  - `test_startup.py`
  - `test_bot_client.py`
- commands
  - `test_system_commands.py`
  - `test_image_commands.py`
  - `test_interject_commands.py`
  - `test_death_commands.py`
- services
  - `test_queue_service.py`
  - `test_state_service.py`
  - `test_message_service.py`
  - `test_runpod_service.py`
  - `test_death_service.py`
  - `test_interject_service.py`
  - `test_auto_update.py`
- mention and prompt handling
  - `test_mention_handler.py`
  - `test_openai_handler.py`
  - `test_message_utils.py`
  - `test_discord_helper.py`
- downloader/media helpers
  - `test_downloader_utils.py`
  - `test_youtube_utils.py`
  - `test_tiktok_utils.py`
  - `test_twitter_utils.py`
  - `test_facebook_utils.py`
  - `test_url_utils.py`
- database/logging
  - `test_db_connection.py`
  - `test_db_logger.py`

## Current downloader/provider flow under test

The URL-enrichment stack now works like this:

- **YouTube**: `youtube_transcript_api`, optional `TRANSCRIPT_PROXY`, persistent cache
- **TikTok**: `yt-dlp` download -> ffmpeg audio extraction -> Groq Whisper transcription, with proxy fallback and persistent cache
- **Twitter/X**: RapidAPI tweet detail/context lookup -> optional Groq Whisper transcription for embedded video audio -> persistent cache
- **Facebook**: `yt-dlp` download -> ffmpeg audio extraction -> Groq Whisper transcription, with proxy fallback and persistent cache
- **Articles**: `httpx` fetch -> `trafilatura` extraction -> `newspaper3k` fallback -> persistent cache

`test_downloader_utils.py` verifies the aggregation/orchestration layer, while the source-specific test modules validate the individual helper behaviors.

## Notes on dependencies

Some tests patch external integrations rather than making live network calls. If you see failures related to missing packages, make sure `test_requirements.txt` has been installed.

## Focused runs

Examples:

```bash
make test-specific TEST=downloader_utils
.venv/bin/python -m pytest tests/test_mention_handler.py
.venv/bin/python -m pytest tests/test_twitter_utils.py -q
```
