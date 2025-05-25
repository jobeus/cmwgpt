import io
import base64
from openai import OpenAI
from config import OPENAI_API_KEY

# Instantiate OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

def get_chat_completion(model: str, messages: list) -> str:
    """
    Gets a chat completion from the OpenAI API.
    """
    response = client.chat.completions.create(model=model, messages=messages)
    return response.choices[0].message.content

def generate_image(prompt: str, model: str, edit_image_bytes: bytes | None = None, edit_image_filename: str | None = None) -> bytes:
    """
    Generates an image using the OpenAI API.
    Can also edit an image if edit_image_bytes and edit_image_filename are provided.
    Returns the raw image bytes.
    """
    b64_json_data = None # Initialize to ensure it's always defined
    if model == 'dall-e-2' or model == 'dall-e-3':
        result = client.images.generate(
            model=model,
            prompt=prompt,
            n=1,
            response_format='b64_json'
        )
        b64_json_data = result.data[0].b64_json
    else: # assume gpt-image-1 or similar custom model that might support edit or generate
        if edit_image_bytes and edit_image_filename:
            file_obj = io.BytesIO(edit_image_bytes)
            # The OpenAI SDK's edit method expects a list of file-like objects for the 'image' parameter.
            # While the 'name' attribute on file_obj isn't strictly required by BytesIO itself,
            # some SDKs or underlying multipart form data encoders might use it.
            # For safety and to align with common practices (like how an actual file object would have a name),
            # we can set it, though it's not explicitly documented as used by the OpenAI Python SDK for BytesIO image edits.
            file_obj.name = edit_image_filename 
            result = client.images.edit(
                model=model,
                image=[file_obj], # image expects a list of file-like objects
                prompt=prompt,
                # n=1 is default for edit, response_format can be 'b64_json' or 'url'
                response_format='b64_json' # Explicitly ask for b64_json
            )
            b64_json_data = result.data[0].b64_json
        else:
            result = client.images.generate(
                model=model,
                prompt=prompt,
                n=1,
                response_format='b64_json' # Ensure b64 for generate too
            )
            b64_json_data = result.data[0].b64_json
            
    if not b64_json_data:
        raise ValueError("Image generation failed, no b64_json data returned.")

    img_bytes = base64.b64decode(b64_json_data)
    return img_bytes
