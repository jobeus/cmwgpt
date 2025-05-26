import base64

from openai import OpenAI
from discord import Attachment

from config import OPENAI_API_KEY, IS_TESTING

# Global client variable for lazy initialization
_client = None


def get_client():
    """Get OpenAI client with lazy initialization."""
    global _client
    if _client is None:
        if IS_TESTING:
            # In testing environment, create a mock-friendly client
            # The actual mocking will be done in tests
            try:
                _client = OpenAI(api_key=OPENAI_API_KEY)
            except Exception:
                # If OpenAI client fails in testing, create a dummy object
                class MockClient:
                    def __getattr__(self, name):  # noqa: ARG002
                        return lambda *args, **kwargs: None  # noqa: ARG005
                _client = MockClient()
        else:
            _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


def get_chat_completion(model: str, messages: list) -> str:
    """
    Gets a chat completion from the OpenAI API.
    """
    client = get_client()
    response = client.responses.create(model=model, input=messages)
    return response.output_text


def generate_image(
        prompt: str,
        model: str,
        edit_image: Attachment | None = None) -> bytes:
    """
    Generates an image using the OpenAI API.
    Can also edit an image if edit_image_bytes and edit_image_filename are provided.
    Returns the raw image bytes.
    """
    client = get_client()
    b64_json_data = None  # Initialize to ensure it's always defined
    result = None
    if model == "dall-e-2" or model == "dall-e-3":
        result = client.images.generate(
            model=model, prompt=prompt, n=1, response_format="b64_json")
    else:  # assume gpt-image-1 or similar custom model that might support edit or generate
        if edit_image:
            file_obj = edit_image.to_file()
            result = client.images.edit(
                model=model,
                image=[file_obj],  # image expects a list of file-like objects
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
