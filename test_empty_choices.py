import asyncio
from openai import AsyncOpenAI
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_empty():
    class MockOpenAI(AsyncOpenAI):
        def __init__(self, *args, **kwargs):
            pass
            
        class chat:
            class completions:
                @staticmethod
                async def create(*args, **kwargs):
                    class Choice:
                        class Message:
                            content = None # Some models return None or empty string when blocked or format fails
                    class Response:
                        choices = [Choice()]
                    return Response()

    client = MockOpenAI()
    
    try:
        response = await client.chat.completions.create()
        if response and response.choices:
            response_text = response.choices[0].message.content
            print(f"Content is: {repr(response_text)}")
            if response_text:
                print("Returning valid text")
            else:
                print("Returning empty string fallback")
                return ""
        
        print("Returning faliled string")
        return "Failed to get a response from the model."
    except Exception as e:
        logger.error(f"Error: {e}")

asyncio.run(test_empty())
