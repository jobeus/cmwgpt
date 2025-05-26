import logging
import io  # Keep for discord.File
import json

import discord
from discord.ext import commands
from discord import app_commands
from discord.app_commands import Choice
from openai import BadRequestError  # Keep for error handling in /draw

from utils.pasters import upload_to_pasters
from utils.discord_helper import get_mention_legend
from openai_handler import get_chat_completion, generate_image
from config import (
    DISCORD_BOT_TOKEN,
    SYSTEM_PROMPT,
    DEFAULT_MODEL,
    DEFAULT_IMAGE_MODEL,
    INCLUDE_USERNAMES,
    REPLY_TO_MENTIONS,
    INCLUDE_NUM_CHATLINES,
)
from bot_state import conversations, models, channel_system_prompts

# Configure root logger to stdout
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s: %(message)s")
logger = logging.getLogger("discord_bot")

# Configure Discord bot with intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="/", intents=intents)


@bot.event
async def on_connect():
    logger.info("Connected to Discord")


@bot.event
async def on_ready():
    await bot.tree.sync()
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")


@bot.event
async def on_disconnect():
    logger.warning("Disconnected from Discord, attempting to reconnect")


@bot.event
async def on_message(message: discord.Message):
    # Ignore bots and DMs
    if message.author.bot or not isinstance(
            message.channel, discord.TextChannel):
        return

    # If bot is mentioned, gather last INCLUDE_NUM_CHATLINES messages and send
    # to OpenAI
    if bot.user and bot.user in message.mentions and REPLY_TO_MENTIONS:
        async with message.channel.typing():
            chat_msgs = await _prepare_mention_context(message, bot.user)
            reply_content = get_chat_completion(
                model=models.get(
                    message.channel.id,
                    DEFAULT_MODEL),
                messages=chat_msgs)
            await _send_channel_reply(message.channel, reply_content)

    # ensure other commands still processed
    await bot.process_commands(message)


async def _prepare_mention_context(
        message: discord.Message, bot_user: discord.User) -> list[dict[str, str]]:
    """Prepares the message list for OpenAI context in case of a mention."""
    logger.info(
        f"Mention by {
            message.author} in #{
            message.channel}: {
                message.content}"
    )
    history_msgs = []
    async for msg in message.channel.history(limit=INCLUDE_NUM_CHATLINES):
        history_msgs.append(msg)
    history_msgs.reverse()  # oldest first

    legend_section = await get_mention_legend(message.channel)

    current_channel_system_prompt = channel_system_prompts.get(
        message.channel.id, SYSTEM_PROMPT)
    current_channel_system_prompt += (
        f"In the channel your ID is: <@{bot_user.id}> and included are the last "
        f"{INCLUDE_NUM_CHATLINES} messages from the channel in JSON format. You can read all of these messages. You've been mentioned in the vert last messabe in the JSON array (but you may have been asked things before, and answered things before, that's ok! just respond to the LAST thing asked or @mentioned to you though please.  "
        f"You are   expected to reply, but less metaphysics and more straight up answers like a user on a 30 year old IRC board and not a talkative robot. Respond with only your the content of your reply.\n\n"
        f"{legend_section}\n\n"
    )
    ask_amble = (
        "Conversation lines are below and represent the last "
        f"{INCLUDE_NUM_CHATLINES} chat lines in the chat. The last one mentions you but feel free to read all the context, then answer the very last line in the following array ONLY. "
        "History is provided in this json array format with { 'user':'<id>', 'says': '<content of message>'}:"
    )

    chat_context = [
        {"role": "system", "content": current_channel_system_prompt}]

    chat_history = []
    for msg in history_msgs:
        chat_history.append(
            {"user": f"<@{msg.author.id}>", "says": msg.content})
    chat_context.append(
        {"role": "user", "content": ask_amble + "\n\n" + json.dumps(chat_history)})

    with open("debug.txt", "w") as f:
        json.dump(chat_context, f, indent=2)
    return chat_context


async def _send_channel_reply(channel: discord.TextChannel, reply_text: str):
    """Sends a reply to a channel, handling potential pasters.rs upload."""
    final_reply = reply_text
    if len(reply_text) > 2000:
        try:
            logger.info(
                "Reply for channel message exceeded 2000 characters, attempting to upload to pasters.rs")
            pasted_url = upload_to_pasters(markdown_text=reply_text)
            final_reply = f"My response was too long to post here, so I've uploaded it to: {pasted_url}"
        except Exception as e:
            logger.error(f"Error uploading to pasters.rs: {e}")
            final_reply = "The content of my response was over 2000 characters (discord limit), and there was a problem uploading it to paste.rs. Sorry, try again later."
    await channel.send(final_reply)


@bot.tree.command(name="reset", description="Reset the conversation history")
async def reset(interaction: discord.Interaction):
    channel_id = interaction.channel.id
    legend_section = await get_mention_legend(interaction.channel)
    conversations[channel_id] = [{"role": "system",
                                  "content": channel_system_prompts.get(channel_id,
                                                                        SYSTEM_PROMPT) + "\n" + legend_section}]
    models[channel_id] = DEFAULT_MODEL
    logger.info(f"[/reset] Channel {channel_id}: conversation reset")
    await interaction.response.defer(ephemeral=False, thinking=True)
    await interaction.followup.send("Conversation reset.", ephemeral=True)


@bot.tree.command(name="model", description="View or set OpenAI model")
@app_commands.describe(model="Model name to use")
@app_commands.choices(
    model=[
        # Choice(name='gpt-4.1', value='gpt-4.1'), # expensive
        Choice(name="gpt-4.1-mini", value="gpt-4.1-mini"),
        Choice(name="gpt-4.1-nano", value="gpt-4.1-nano"),
        Choice(name="gpt-4o-mini", value="gpt-4o-mini"),
    ]
)
async def set_model(
        interaction: discord.Interaction,
        model: str | None = None):
    channel_id = interaction.channel.id
    await interaction.response.defer(ephemeral=False, thinking=True)
    if model:
        models[channel_id] = model
        logger.info(f"[/model] Channel {channel_id}: model set to {model}")
        await interaction.followup.send(f"Model set to `{model}`.", ephemeral=True)
    else:
        model = models.get(channel_id, DEFAULT_MODEL)
        await interaction.followup.send(f"Model is `{model}`.", ephemeral=True)


# Define the systemprompt command group
systemprompt_group = app_commands.Group(
    name="systemprompt",
    description="Manage channel-specific system prompt")


@systemprompt_group.command(name="set",
                            description="View or set the system prompt for this channel")
@app_commands.describe(prompt_text="The new system prompt. Omit to view current prompt.")
async def systemprompt_set(
        interaction: discord.Interaction,
        prompt_text: str | None = None):
    channel_id = interaction.channel.id
    await interaction.response.defer(ephemeral=True, thinking=True)
    legend_section = await get_mention_legend(interaction.channel)

    if prompt_text:
        channel_system_prompts[channel_id] = prompt_text
        if channel_id in conversations and conversations[channel_id]:
            if conversations[channel_id][0]["role"] == "system":
                conversations[channel_id][0]["content"] = prompt_text
            else:
                conversations[channel_id].insert(
                    0, {"role": "system", "content": prompt_text})
        else:
            conversations.setdefault(channel_id, []).insert(
                0, {"role": "system", "content": prompt_text})

        logger.info(
            f"[/systemprompt set] Channel {channel_id}: system prompt updated.")
        await interaction.followup.send(
            "System prompt updated for this channel. The new prompt will be used for future messages and context.",
            ephemeral=True,
        )
    else:
        current_prompt = channel_system_prompts.get(
            channel_id, SYSTEM_PROMPT + "\n" + legend_section)
        logger.info(
            f"[/systemprompt set] Channel {channel_id}: displayed current system prompt.")
        await interaction.followup.send(
            f"Current system prompt for this channel:\n```\n{current_prompt}\n```", ephemeral=True
        )


@systemprompt_group.command(name="reset",
                            description="Reset the system prompt for this channel to the default")
async def systemprompt_reset(interaction: discord.Interaction):
    channel_id = interaction.channel.id
    await interaction.response.defer(ephemeral=True, thinking=True)

    if channel_id in channel_system_prompts:
        del channel_system_prompts[channel_id]
        logger.info(
            f"[/systemprompt reset] Channel {channel_id}: custom prompt removed, reverting to default.")

    if channel_id in conversations and conversations[
            channel_id] and conversations[channel_id][0]["role"] == "system":
        conversations[channel_id][0]["content"] = SYSTEM_PROMPT
    else:
        # Ensure conversation list exists and prepend system prompt
        conversations.setdefault(channel_id, []).insert(
            0, {"role": "system", "content": SYSTEM_PROMPT})
        # If the conversation existed but didn't start with a system prompt, this ensures the new system prompt is first.
        # If it was already first, this path isn't taken. If it was empty or didn't exist, it's created with the system prompt.
        # To avoid duplicate system prompts if one was already there but not at index 0 (which is an unlikely state):
        # We can filter out any other system prompts after ensuring the first
        # one is correct.
        if len(conversations[channel_id]
               ) > 1:  # if more than just our newly inserted prompt
            # Keep the first (our new/updated one) and any non-system messages
            new_convo = [conversations[channel_id][0]]
            for msg in conversations[channel_id][1:]:
                if msg["role"] != "system":
                    new_convo.append(msg)
            conversations[channel_id] = new_convo

    logger.info(
        f"[/systemprompt reset] Channel {channel_id}: system prompt reset to default.")
    await interaction.followup.send("System prompt for this channel has been reset to the default.", ephemeral=True)


bot.tree.add_command(systemprompt_group)


@bot.tree.command(name="chat", description="Send a message to the chatbot")
@app_commands.describe(message="Your message",
                       attachment="Optional image to attach to the prompt")
async def chat(interaction: discord.Interaction, message: str,
               attachment: discord.Attachment | None = None):
    channel_id = interaction.channel.id
    legend_section = await get_mention_legend(interaction.channel)

    if INCLUDE_USERNAMES:
        message = interaction.user.display_name + " says: " + message
    # Initialize if missing
    if channel_id not in conversations:
        conversations[channel_id] = [{"role": "system", "content": channel_system_prompts.get(
            channel_id, SYSTEM_PROMPT + "\n" + legend_section)}]
        models[channel_id] = DEFAULT_MODEL
        logger.info(
            f"[/chat] Channel {channel_id}: initialized conversation and model")

    # Construct content payload for OpenAI
    if attachment:
        logger.info(
            f"[/chat] Channel {channel_id}: including image URL {attachment.url}")
        content_payload = [
            {"type": "text", "text": message},
            {"type": "image_url", "image_url": {"url": attachment.url}},
        ]
    else:
        content_payload = message

    # Log user input
    logger.info(f"[/chat] Channel {channel_id} User: {message}")
    conversations[channel_id].append(
        {"role": "user", "content": json.dumps(content_payload)})

    # Acknowledge and send prompt-only message
    await interaction.response.defer(ephemeral=False, thinking=True)

    # Typing indicator while waiting for OpenAI
    async with interaction.channel.typing():
        reply = get_chat_completion(
            model=models.get(
                channel_id,
                DEFAULT_MODEL),
            messages=conversations[channel_id])

        # Log and store assistant reply
        logger.info(f"[/chat] Channel {channel_id} Assistant: {reply}")
        conversations[channel_id].append(
            {"role": "assistant", "content": json.dumps(reply)})

        # Prepare base message content (original prompt + attachment if any)
        base_interaction_message = ""
        if attachment:
            base_interaction_message = f"{attachment.url}\n> {message}"
        else:
            base_interaction_message = f"> {message}"

        await _send_interaction_followup(interaction, base_interaction_message, reply)


@bot.tree.command(name="draw", description="Generate an image from a prompt")
@app_commands.describe(
    prompt="Prompt for image generation",
    edit_image="Optional image to edit",
    model="Optional image model to use",
)
@app_commands.choices(
    model=[
        Choice(name="gpt-image-1", value="gpt-image-1"),
        Choice(name="dall-e-2", value="dall-e-2"),
        Choice(name="dall-e-3", value="dall-e-3"),
    ]
)
async def draw(
    interaction: discord.Interaction,
    prompt: str,
    edit_image: discord.Attachment | None = None,
    # This is a parameter, not from config directly here
    model: str = DEFAULT_IMAGE_MODEL,
):
    channel_id = interaction.channel.id  # Used for logging
    logger.info(
        f"[/draw] Channel {channel_id} Prompt: {prompt} Model: {model} Edit? {bool(edit_image)}")

    # Typing indicator while generating image
    async with interaction.channel.typing():
        # Acknowledge and send prompt-only message
        await interaction.response.defer(ephemeral=False, thinking=True)
        try:
            if edit_image:
                logger.info(
                    f"[/draw] Channel {channel_id}: editing image {edit_image.filename}")

            # Call the helper function from openai_handler.py
            img_bytes = generate_image(
                prompt=prompt, model=model, edit_image=edit_image)

            # Log image generation
            logger.info(f"[/draw] Channel {channel_id}: image generated")
            file = discord.File(io.BytesIO(img_bytes), filename="image.png")

            if edit_image:
                await interaction.followup.send(content=f"{edit_image.url}\n> {prompt}", file=file)
            else:
                await interaction.followup.send(content=f"> {prompt}", file=file)
        except BadRequestError as e:
            logger.error(f"OpenAI API BadRequestError in draw command: {e}")
            error_message = f"> {prompt}\n\nSorry, your request was rejected by the safety system. Details: {
                e.error.message if e.error else 'No specific error message provided by API.'}"
            await interaction.followup.send(content=error_message)


async def _send_interaction_followup(
        interaction: discord.Interaction,
        base_content: str,
        reply_text: str):
    """Sends a followup to an interaction, handling potential pasters.rs upload for the reply_text part."""
    final_content = base_content

    # Check if adding the reply_text makes the whole message too long
    if len(base_content + f"\n{reply_text}") > 2000:
        try:
            logger.info(
                "Reply for interaction followup exceeded 2000 characters with base_content, attempting to upload to pasters.rs"
            )
            pasted_url = upload_to_pasters(markdown_text=reply_text)
            # Add the pasters link for the reply part
            final_content += f"\n\nMy detailed response was too long, so I've uploaded it here: {pasted_url}"
            suppress_embeds = True  # Often good when sending links explicitly
        except Exception as e:
            logger.error(f"Error uploading to pasters.rs for interaction: {e}")
            final_content += "\n\nThe content of my response was over 2000 characters, and there was a problem uploading it. Sorry, try again later."
            suppress_embeds = False
        await interaction.followup.send(content=final_content, suppress_embeds=suppress_embeds)
    else:
        final_content += f"\n{reply_text}"
        await interaction.followup.send(content=final_content)


if __name__ == "__main__":
    logger.info("Starting bot...")
    bot.run(DISCORD_BOT_TOKEN)
    logger.info("Bot shutdown.")
