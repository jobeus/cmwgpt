import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
SYSTEM_PROMPT = os.getenv('SYSTEM_PROMPT', 'You are a helpful assistant.')
DEFAULT_MODEL = os.getenv('DEFAULT_MODEL', 'gpt-4.1-nano')
DEFAULT_IMAGE_MODEL = os.getenv('DEFAULT_IMAGE_MODEL', 'gpt-image-1')
INCLUDE_USERNAMES = os.getenv('INCLUDE_USERNAMES','True').lower() in ('true', '1')
REPLY_TO_MENTIONS = os.getenv('REPLY_TO_MENTIONS','True').lower() in ('true', '1')
INCLUDE_NUM_CHATLINES = int(os.getenv('INCLUDE_NUM_CHATLINES', 100))
