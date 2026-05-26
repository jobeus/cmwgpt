import base64
import httpx
import pytest
from unittest.mock import AsyncMock, patch

from src.utils.http_client import (
    create_async_client,
    create_sync_client,
    flush_pending_logs,
)


@pytest.fixture
def mock_log_api_request():
    with patch("src.utils.http_client.log_api_request", new_callable=AsyncMock) as mock:
        yield mock


@pytest.mark.asyncio
async def test_async_transport_success(mock_log_api_request):
    """Test that a successful async request logs everything correctly."""
    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json", "X-Custom": "test"},
                text='{"status": "ok"}',
                request=request,
            )

        async def aclose(self):
            pass

    client = create_async_client(transport=MockTransport(), service_name="test_service")
    
    response = await client.post(
        "https://api.example.com/data",
        headers={"X-Req": "abc"},
        json={"req": "data"}
    )
    
    assert response.status_code == 200
    mock_log_api_request.assert_awaited_once()
    
    call_kwargs = mock_log_api_request.call_args.kwargs
    assert call_kwargs["service_name"] == "test_service"
    assert call_kwargs["method"] == "POST"
    assert call_kwargs["endpoint_url"] == "https://api.example.com/data"
    assert call_kwargs["request_headers"].get("x-req") == "abc"
    assert call_kwargs["request_body"] == '{"req":"data"}'
    assert call_kwargs["response_status"] == 200
    assert call_kwargs["response_headers"].get("content-type") == "application/json"
    assert call_kwargs["response_body"] == '{"status": "ok"}'


@pytest.mark.asyncio
async def test_async_transport_exception(mock_log_api_request):
    """Test that a transport exception logs an error row."""
    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectTimeout("Connection timed out")

        async def aclose(self):
            pass

    client = create_async_client(transport=MockTransport())
    
    with pytest.raises(httpx.ConnectTimeout):
        await client.get("https://api.groq.com/test")
        
    mock_log_api_request.assert_awaited_once()
    call_kwargs = mock_log_api_request.call_args.kwargs
    assert call_kwargs["service_name"] == "groq"  # inferred correctly
    assert call_kwargs["response_status"] == 0
    assert "ConnectTimeout" in call_kwargs["response_body"]


@pytest.mark.asyncio
async def test_sync_transport_buffers_and_flushes(mock_log_api_request):
    """Test that the sync transport buffers logs and flush writes them."""
    class MockSyncTransport(httpx.BaseTransport):
        def handle_request(self, request):
            return httpx.Response(201, text="created", request=request)
            
        def close(self):
            pass

    client = create_sync_client(transport=MockSyncTransport())
    client.get("https://openrouter.ai/test")
    
    transport = client._transport
    assert len(transport.pending_logs) == 1
    
    log_entry = transport.pending_logs[0]
    assert log_entry["service_name"] == "openrouter"
    
    mock_log_api_request.assert_not_called()
    
    await flush_pending_logs(transport)
    assert len(transport.pending_logs) == 0
    mock_log_api_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_domain_exclusion(mock_log_api_request):
    """Test that excluded domains are skipped."""
    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(200, request=request)

        async def aclose(self):
            pass

    client = create_async_client(transport=MockTransport())
    await client.get("https://cdn.discordapp.com/attachments/123/456/img.png")
    await client.get("https://media.discordapp.net/test.jpg")
    await client.get("https://wikimedia.org/api/rest_v1/")
    
    mock_log_api_request.assert_not_called()


@pytest.mark.asyncio
async def test_path_exclusion(mock_log_api_request):
    """Test that specific Wikipedia paths are excluded."""
    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(200, request=request)

        async def aclose(self):
            pass

    client = create_async_client(transport=MockTransport())
    await client.get("https://en.wikipedia.org/wiki/Deaths_in_2024")
    mock_log_api_request.assert_not_called()
    
    # But a normal wikipedia URL should be logged
    await client.get("https://en.wikipedia.org/wiki/Python_(programming_language)")
    assert mock_log_api_request.call_count == 1


@pytest.mark.asyncio
async def test_binary_response_base64_encoding(mock_log_api_request):
    """Test that binary responses are base64-encoded as data URLs."""
    binary_data = b"\\x89PNG\\r\\n\\x1a\\n\\x00\\x00\\x00\\rIHDR"
    
    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(
                200,
                headers={"Content-Type": "image/png"},
                content=binary_data,
                request=request,
            )

        async def aclose(self):
            pass

    client = create_async_client(transport=MockTransport())
    await client.get("https://api.runpod.ai/v2/some-image")
    
    mock_log_api_request.assert_awaited_once()
    call_kwargs = mock_log_api_request.call_args.kwargs
    
    expected_b64 = base64.b64encode(binary_data).decode("ascii")
    expected_data_url = f"data:image/png;base64,{expected_b64}"
    
    assert call_kwargs["response_body"] == expected_data_url
