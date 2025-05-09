# ChatGPT Discord Bot
# Setup
## Prerequisites
* **Python 3.9 or later**
* **Rename the file `env.example` to `.env`**
* Running `pip3 install -r requirements.txt` to install the required dependencies
---
## Step 1: Create a Discord bot

1. Go to https://discord.com/developers/applications create an application
2. Build a Discord bot under the application
3. Get the token from bot setting

   ![image](https://user-images.githubusercontent.com/89479282/205949161-4b508c6d-19a7-49b6-b8ed-7525ddbef430.png)
4. Store the token to `.env` under the `DISCORD_BOT_TOKEN`

   <img height="190" width="390" alt="image" src="https://user-images.githubusercontent.com/89479282/222661803-a7537ca7-88ae-4e66-9bec-384f3e83e6bd.png">

5. Turn MESSAGE CONTENT INTENT `ON`

   ![image](https://user-images.githubusercontent.com/89479282/205949323-4354bd7d-9bb9-4f4b-a87e-deb9933a89b5.png)

6. Invite your bot to your server via OAuth2 URL Generator

   ![image](https://user-images.githubusercontent.com/89479282/205949600-0c7ddb40-7e82-47a0-b59a-b089f929d177.png)


## Step 2: Configure OpenAI API

1. Obtain your API key by visiting https://platform.openai.com/api-keys
2. Paste the API key under `OPENAI_KEY` in `.env`

## Step 3: Run the bot 

Run `python3 bot.py` or `python bot.py` to run the bot

* `/chat [message] [optional attachment]` Chat with ChatGPT/Gemini
* `/draw [prompt] [optional editable image for gpt-1 images] [optional model]` Generate an image with Gemini/OpenAI/Bing
* `/reset` Clear ChatGPT conversation history
* `/model` Switch or view chat model
