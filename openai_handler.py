import io
import base64
from openai import OpenAI
from config import OPENAI_API_KEY
from discord import Attachment

# Instantiate OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

def get_chat_completion(model: str, messages: list) -> str:
    """
    Gets a chat completion from the OpenAI API.
    """
    response = client.responses.create(model=model, input=messages)
    return response.output_text

def generate_image(prompt: str, model: str, edit_image: Attachment | None = None) -> bytes:
    """
    Generates an image using the OpenAI API.
    Can also edit an image if edit_image_bytes and edit_image_filename are provided.
    Returns the raw image bytes.
    """
    b64_json_data = None # Initialize to ensure it's always defined
    result = None
    if model == 'dall-e-2' or model == 'dall-e-3':
        result = client.images.generate(
            model=model,
            prompt=prompt,
            n=1,
            response_format='b64_json'
        )
    else: # assume gpt-image-1 or similar custom model that might support edit or generate
        if edit_image:
            file_obj = edit_image.to_file()
            result = client.images.edit(
                model=model,
                image=[file_obj], # image expects a list of file-like objects
                prompt=prompt,
            )
        else:
            result = client.images.generate(
                model=model,
                prompt=prompt,
                n=1,
            )
    
    if result:
        b64_json_data = result.data[0].b64_json
            
    if not b64_json_data:
        raise ValueError("Image generation failed, no b64_json data returned.")

    img_bytes = base64.b64decode(b64_json_data)
    return img_bytes
