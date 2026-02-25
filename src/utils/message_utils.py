"""
Message formatting utility functions.

Extracted from service classes to follow the principle of separating
stateless utility functions from stateful service classes.
"""

import discord


def format_attachment_message(
        attachment: discord.Attachment,
        message: str) -> str:
    """Format a message with an attachment URL."""
    return f"{attachment.url}\n> {message}"


def format_prompt_message(message: str) -> str:
    """Format a prompt message."""
    return f"> {message}"


def clean_openai_response(response: str) -> str:
    """
    Clean OpenAI response by removing unwanted quote wrapping and fixing escaped characters.

    Args:
        response: Raw response from OpenAI

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

    # Handle escaped characters that should be unescaped
    # Only unescape common cases that OpenAI might escape unnecessarily
    cleaned = cleaned.replace("\\n", "\n")
    cleaned = cleaned.replace("\\t", "\t")
    cleaned = cleaned.replace('\\"', '"')
    cleaned = cleaned.replace("\\\\", "\\")

    return cleaned
