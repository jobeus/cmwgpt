import os
import io
import base64
import logging
import asyncio

from dotenv import load_dotenv
import discord
from discord.ext import commands
from discord import app_commands
from discord.app_commands import Choice
from openai import OpenAI
from utils.pasters import upload_to_pasters

# Configure root logger to stdout
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s:%(name)s: %(message)s'
)
logger = logging.getLogger('discord_bot')

# Load environment variables
load_dotenv()
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
SYSTEM_PROMPT = os.getenv('SYSTEM_PROMPT', 'You are a helpful assistant.')
DEFAULT_MODEL = os.getenv('DEFAULT_MODEL', 'gpt-4.1-nano')
DEFAULT_IMAGE_MODEL = os.getenv('DEFAULT_IMAGE_MODEL', 'gpt-image-1')
INCLUDE_USERNAMES = os.getenv('INCLUDE_USERNAMES','True').lower() in ('true', '1')
REPLY_TO_MENTIONS = os.getenv('REPLY_TO_MENTIONS','True').lower() in ('true', '1')
INCLUDE_NUM_CHATLINES = int(os.getenv('INCLUDE_NUM_CHATLINES', 100))

# Instantiate OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

# Configure Discord bot with intents
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

# In-memory storage for conversation and model per channel
conversations: dict[int, list[dict[str, any]]] = {}
models: dict[int, str] = {}
channel_system_prompts: dict[int, str] = {}

@bot.event
async def on_connect():
    logger.info('Connected to Discord')

@bot.event
async def on_ready():
    await bot.tree.sync()
    logger.info(f'Logged in as {bot.user} (ID: {bot.user.id})')

@bot.event
async def on_disconnect():
    logger.warning('Disconnected from Discord, attempting to reconnect')

@bot.event
async def on_message(message: discord.Message):
    # Ignore bots and DMs
    if message.author.bot or not isinstance(message.channel, discord.TextChannel):
        return

    # If bot is mentioned, gather last INCLUDE_NUM_CHATLINES messages and send to OpenAI
    if bot.user in message.mentions and REPLY_TO_MENTIONS:
        logger.info(f'Mention by {message.author} in #{message.channel}: {message.content}')
        # Fetch history
        history_msgs = []
        async for msg in message.channel.history(limit=INCLUDE_NUM_CHATLINES):
            history_msgs.append(msg)
        history_msgs = list(reversed(history_msgs))

        # Build OpenAI messages
        current_system_prompt = channel_system_prompts.get(message.channel.id, SYSTEM_PROMPT)
        chat_msgs = [{'role': 'system', 'content': current_system_prompt + '\n\n' + f'In the channel your name is: {bot.user.display_name} and included are the last {INCLUDE_NUM_CHATLINES} messages from the channel, the last line is obviously mentioning you and thats what you should respond to now.'}]
        current_system_prompt = channel_system_prompts.get(message.channel.id, SYSTEM_PROMPT)
        for msg in history_msgs:
            chat_msgs.append({
                'role': 'user',
                'content': f"{msg.author.display_name}: {msg.content}"
            })

        # Indicate typing
        async with message.channel.typing():
            response = client.chat.completions.create(
                model=models.get(message.channel.id, DEFAULT_MODEL),
                messages=chat_msgs
            )
            reply = response.choices[0].message.content
            logger.info(f'Reply to mention: {reply}')
            if len(reply) > 2000:
                try:
                    logger.info(f'')
                    reply = upload_to_pasters(markdown_text=reply)
                except Exception:
                    reply = "The content of my response was over 2000 characters and there was a problem uploading to paste.rs, sorry try again later"
            await message.channel.send(reply)

    # ensure other commands still processed
    await bot.process_commands(message)

@bot.tree.command(name='reset', description='Reset the conversation history')
async def reset(interaction: discord.Interaction):
    channel_id = interaction.channel.id
    conversations[channel_id] = [{'role': 'system', 'content': channel_system_prompts.get(channel_id, SYSTEM_PROMPT)}]
    models[channel_id] = DEFAULT_MODEL
    logger.info(f'[/reset] Channel {channel_id}: conversation reset')
    await interaction.response.defer(ephemeral=False, thinking=True)
    await interaction.followup.send('Conversation reset.', ephemeral=True)


@bot.tree.command(name='model', description='View or set OpenAI model')
@app_commands.describe(model='Model name to use')
@app_commands.choices(model=[
    # Choice(name='gpt-4.1', value='gpt-4.1'), # expensive
    Choice(name='gpt-4.1-mini', value='gpt-4.1-mini'),
    Choice(name='gpt-4.1-nano', value='gpt-4.1-nano'),
    Choice(name='gpt-4o-mini', value='gpt-4o-mini')
])
async def set_model(interaction: discord.Interaction, model: str | None = None):
    channel_id = interaction.channel.id
    await interaction.response.defer(ephemeral=False, thinking=True)
    if model:
        models[channel_id] = model
        logger.info(f'[/model] Channel {channel_id}: model set to {model}')
        await interaction.followup.send(f'Model set to `{model}`.', ephemeral=True)
    else:
        model = models.get(channel_id, DEFAULT_MODEL)
        await interaction.followup.send(f'Model is `{model}`.', ephemeral=True)

# Define the systemprompt command group
systemprompt_group = app_commands.Group(name='systemprompt', description='Manage channel-specific system prompt')

@systemprompt_group.command(name='set', description='View or set the system prompt for this channel')
@app_commands.describe(prompt_text='The new system prompt. Omit to view current prompt.')
async def systemprompt_set(interaction: discord.Interaction, prompt_text: str | None = None):
    channel_id = interaction.channel.id
    await interaction.response.defer(ephemeral=True, thinking=True)

    if prompt_text:
        channel_system_prompts[channel_id] = prompt_text
        if channel_id in conversations and conversations[channel_id]:
            if conversations[channel_id][0]['role'] == 'system':
                conversations[channel_id][0]['content'] = prompt_text
            else:
                conversations[channel_id].insert(0, {'role': 'system', 'content': prompt_text})
        else:
            conversations.setdefault(channel_id, []).insert(0, {'role': 'system', 'content': prompt_text})
        
        logger.info(f'[/systemprompt set] Channel {channel_id}: system prompt updated.')
        await interaction.followup.send(f'System prompt updated for this channel. The new prompt will be used for future messages and context.', ephemeral=True)
    else:
        current_prompt = channel_system_prompts.get(channel_id, SYSTEM_PROMPT)
        logger.info(f'[/systemprompt set] Channel {channel_id}: displayed current system prompt.')
        await interaction.followup.send(f'Current system prompt for this channel:\n```\n{current_prompt}\n```', ephemeral=True)

@systemprompt_group.command(name='reset', description='Reset the system prompt for this channel to the default')
async def systemprompt_reset(interaction: discord.Interaction):
    channel_id = interaction.channel.id
    await interaction.response.defer(ephemeral=True, thinking=True)

    if channel_id in channel_system_prompts:
        del channel_system_prompts[channel_id]
        logger.info(f'[/systemprompt reset] Channel {channel_id}: custom prompt removed, reverting to default.')

    if channel_id in conversations and conversations[channel_id] and conversations[channel_id][0]['role'] == 'system':
        conversations[channel_id][0]['content'] = SYSTEM_PROMPT
    else:
        # Ensure conversation list exists and prepend system prompt
        conversations.setdefault(channel_id, []).insert(0, {'role': 'system', 'content': SYSTEM_PROMPT})
        # If the conversation existed but didn't start with a system prompt, this ensures the new system prompt is first.
        # If it was already first, this path isn't taken. If it was empty or didn't exist, it's created with the system prompt.
        # To avoid duplicate system prompts if one was already there but not at index 0 (which is an unlikely state):
        # We can filter out any other system prompts after ensuring the first one is correct.
        if len(conversations[channel_id]) > 1: # if more than just our newly inserted prompt
             # Keep the first (our new/updated one) and any non-system messages
            new_convo = [conversations[channel_id][0]] 
            for msg in conversations[channel_id][1:]:
                if msg['role'] != 'system':
                    new_convo.append(msg)
            conversations[channel_id] = new_convo


    logger.info(f'[/systemprompt reset] Channel {channel_id}: system prompt reset to default.')
    await interaction.followup.send('System prompt for this channel has been reset to the default.', ephemeral=True)

bot.tree.add_command(systemprompt_group)

@bot.tree.command(name='chat', description='Send a message to the chatbot')
@app_commands.describe(
    message='Your message',
    attachment='Optional image to attach to the prompt'
)
async def chat(
    interaction: discord.Interaction,
    message: str,
    attachment: discord.Attachment | None = None
):
    channel_id = interaction.channel.id
    if INCLUDE_USERNAMES:
        message = interaction.user.display_name + " says: " + message
    # Initialize if missing
    if channel_id not in conversations:
        conversations[channel_id] = [{'role': 'system', 'content': channel_system_prompts.get(channel_id, SYSTEM_PROMPT)}]
        models[channel_id] = DEFAULT_MODEL
        logger.info(f'[/chat] Channel {channel_id}: initialized conversation and model')

    # Construct content payload for OpenAI
    if attachment:
        logger.info(f'[/chat] Channel {channel_id}: including image URL {attachment.url}')
        content_payload = [
            {'type': 'text', 'text': message},
            {'type': 'image_url', 'image_url': {'url': attachment.url}}
        ]
    else:
        content_payload = message

    # Log user input
    logger.info(f'[/chat] Channel {channel_id} User: {message}')
    conversations[channel_id].append({'role': 'user', 'content': content_payload})

    # Acknowledge and send prompt-only message
    await interaction.response.defer(ephemeral=False, thinking=True)

    # Typing indicator while waiting for OpenAI
    async with interaction.channel.typing():
        response = client.chat.completions.create(
            model=models[channel_id],
            messages=conversations[channel_id]
        )
        reply = response.choices[0].message.content

        # Log and store assistant reply
        logger.info(f'[/chat] Channel {channel_id} Assistant: {reply}')
        conversations[channel_id].append({'role': 'assistant', 'content': reply})

        # Edit original message to include reply
        if attachment:
            combined = f"{attachment.url}\n> {message}"
        else:
            combined = f"> {message}"
        if len(combined + f"\n{reply}") > 2000:
            try:
                combined += "\n\n" + upload_to_pasters(markdown_text=reply)
            except Exception:
                combined += "\nThe content was over 2000 characters and there was a problem uploading to paste.rs, sorry try again later"
            await interaction.followup.send(content=combined, suppress_embeds=True)
        else:
            combined += f"\n{reply}"
            await interaction.followup.send(content=combined)


@bot.tree.command(name='draw', description='Generate an image from a prompt')
@app_commands.describe(
    prompt='Prompt for image generation',
    edit_image='Optional image to edit',
    model='Optional image model to use',
)
@app_commands.choices(model=[
    Choice(name='gpt-image-1', value='gpt-image-1'),
    Choice(name='dall-e-2', value='dall-e-2'),
    Choice(name='dall-e-3', value='dall-e-3')
])
async def draw(
    interaction: discord.Interaction,
    prompt: str,
    edit_image: discord.Attachment | None = None,
    model: str = DEFAULT_IMAGE_MODEL
):
    channel_id = interaction.channel.id
    logger.info(f'[/draw] Channel {channel_id} Prompt: {prompt} Model: {model} Edit? {bool(edit_image)}')

    # Typing indicator while generating image
    async with interaction.channel.typing():
        # Acknowledge and send prompt-only message
        await interaction.response.defer(ephemeral=False, thinking=True)
        if model == 'dall-e-2' or model == 'dall-e-3':
            result = client.images.generate(
                model=model,
                prompt=prompt,
                n=1,
                response_format='b64_json'
            )
        else:
            if edit_image:
                img_bytes = await edit_image.read()
                file_obj = io.BytesIO(img_bytes)
                file_obj.name = edit_image.filename
                logger.info(f'[/draw] Channel {channel_id}: editing image {edit_image.filename}')
                result = client.images.edit(
                    model=model,
                    image=[file_obj],
                    prompt=prompt
                )
            else:
                result = client.images.generate(
                    model=model,
                    prompt=prompt,
                    n=1
                )
        b64 = result.data[0].b64_json
        img_bytes = base64.b64decode(b64)

        # Log image generation
        logger.info(f'[/draw] Channel {channel_id}: image generated')
        file = discord.File(io.BytesIO(img_bytes), filename='image.png')

        if edit_image:
            await interaction.followup.send(content=f"{edit_image.url}\n> {prompt} ", file=file)
        else:
            await interaction.followup.send(content=f"> {prompt} ", file=file)


if __name__ == '__main__':
    logger.info('Starting bot...')
    bot.run(DISCORD_BOT_TOKEN)
    logger.info('Bot shutdown.')
