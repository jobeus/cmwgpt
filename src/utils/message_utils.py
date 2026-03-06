"""
Message formatting utility functions.

Extracted from service classes to follow the principle of separating
stateless utility functions from stateful service classes.
"""

import discord
import re

from typing import Union, List


def format_attachment_message(
        attachment: Union[discord.Attachment, List[discord.Attachment]],
        message: str) -> str:
    """Format a message with one or more attachment URLs."""
    if isinstance(attachment, list):
        urls = "\n".join(
            f"> {att.url}" for att in attachment if att is not None)
        return f"{urls}\n> {message}"
    return f"> {attachment.url}\n> {message}"


def format_prompt_message(message: str) -> str:
    """Format a prompt message."""
    return f"> {message}"


def clean_openai_response(response: str, bot_id: int = None) -> str:
    """
    Clean OpenAI response by removing unwanted quote wrapping and fixing escaped characters.

    Args:
        response: Raw response from OpenAI
        bot_id: The ID of the bot user (to strip specific bot mentions)

    Returns:
        Cleaned response string
    """
    if not response:
        return response

    # Strip leading/trailing whitespace
    cleaned = response.strip()

    # Check if the entire response is wrapped in quotes
    # We need to be more careful about counting quotes to handle escaped quotes
    if len(cleaned) >= 2 and cleaned.startswith('"') and cleaned.endswith('"'):

        # Count unescaped quotes to see if it's just outer wrapping
        quote_count = 0
        i = 0
        while i < len(cleaned):
            if cleaned[i] == '"':
                # Check if this quote is escaped
                if i == 0 or cleaned[i - 1] != "\\":
                    quote_count += 1
                # Handle the case where the backslash itself is escaped
                elif i >= 2 and cleaned[i - 2: i] == "\\\\":
                    quote_count += 1
            i += 1

        # If there are only 2 unescaped quotes (start and end), remove them
        if quote_count == 2:
            cleaned = cleaned[1:-1]

    # Fix broken mentions like <@1234: -> <@1234>:
    cleaned = re.sub(r"<@(\d+)([^0-9>])", r"<@\1>\2", cleaned)
    cleaned = re.sub(r"<@(\d+)$", r"<@\1>", cleaned)

    # Restore old generic pattern stripping for ALL responses:
    # Matches patterns like:
    # [2024-01-01 12:00:00] [123456] <@12345>:
    # [123456] <@12345>:
    # <@12345>:
    prefix_pattern = r"^(?:\[.*?\]\s*)*<@[0-9]+>:\s*"
    # Also catch cases where it just spits out the timestamp and ID
    fallback_pattern = r"^(?:\[.*?\]\s*)*\[[0-9]+\]\s*"

    no_prefix = re.sub(prefix_pattern, "", cleaned.strip())
    if no_prefix == cleaned.strip():
        no_prefix = re.sub(fallback_pattern, "", no_prefix)
    cleaned = no_prefix.strip()

    # Now strip the specific bot patterns aggressively if bot_id is provided
    bot_names = r"(?:cmwgpt|bot|assistant|ai|user|system)"
    
    if bot_id:
        specific_bot_pattern = re.compile(
            fr"^(?:"
            fr"<@{bot_id}>[:]?\s*|"       # <@bot_id> or <@bot_id>:
            fr"{bot_names}\s*:\s*|"       # cmwgpt: or bot:
            fr"\[.*?\]\s*"                # Bracketed prefixes (again, just in case)
            fr")+",
            re.IGNORECASE
        )
    else:
        # If no bot_id, just strip bot names
        specific_bot_pattern = re.compile(
            fr"^(?:"
            fr"{bot_names}\s*:\s*|"       # cmwgpt: or bot:
            fr"\[.*?\]\s*"                # Bracketed prefixes
            fr")+",
            re.IGNORECASE
        )

    original_cleaned = cleaned
    cleaned = specific_bot_pattern.sub("", cleaned).strip()
    
    if not cleaned:
        cleaned = original_cleaned.strip()

    # Handle escaped characters that should be unescaped
    # Only unescape common cases that OpenAI might escape unnecessarily
    cleaned = cleaned.replace("\\n", "\n")
    cleaned = cleaned.replace("\\t", "\t")
    cleaned = cleaned.replace('\\"', '"')
    cleaned = cleaned.replace("\\\\", "\\")

    return cleaned
