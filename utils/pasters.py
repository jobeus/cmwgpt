import io
import requests


def upload_to_pasters(markdown_text: str) -> str:
    response = requests.post(
        "https://paste.rs",
        data=io.BytesIO(
            markdown_text.encode('utf-8')))

    if response.status_code == 201:
        return response.text.strip() + ".md"
    else:
        raise Exception(
            f"paste.rs error: {response.status_code} - {response.text}")
