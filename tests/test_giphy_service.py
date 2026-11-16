"""Tests for the Giphy search, filmstrip and download helpers."""

import io
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from PIL import Image

from src.services import giphy_service
from src.services.giphy_service import (
    FILMSTRIP_FRAME_WIDTH, MAX_POST_BYTES, Gif, GiphyError, download_gif, filmstrip, get_gif, gif_filmstrip,
    search_gifs,
)
from src.utils.http_client import ResponseTooLarge


def _gif_bytes(frames, size=(200, 100)):
    images = [Image.new("RGB", size, (index * 20 % 256, 0, 0)) for index in range(frames)]
    out = io.BytesIO()
    images[0].save(out, format="GIF", save_all=True, append_images=images[1:], duration=50)
    return out.getvalue()


def _item(gif_id="abc123", **sizes):
    sizes = sizes or {"original": 5000, "downsized": 900, "fixed_width": 300}
    images = {name: {"url": f"https://media.giphy.com/{name}.gif", "size": str(size), "width": "480", "height": "270"}
              for name, size in sizes.items()}
    return {"id": gif_id, "title": "Mic Drop GIF", "url": f"https://giphy.com/gifs/mic-drop-{gif_id}", "images": images}


def _api(status=200, data=None):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=httpx.Response(status, json={"data": data}))
    return patch.object(giphy_service.httpx, "AsyncClient", return_value=client), client


class TestFilmstrip(unittest.TestCase):
    def test_tiles_ten_frames_in_two_rows(self):
        sheet = Image.open(io.BytesIO(filmstrip(_gif_bytes(30))))
        self.assertEqual(sheet.format, "JPEG")
        self.assertEqual(sheet.size, (5 * FILMSTRIP_FRAME_WIDTH, 2 * FILMSTRIP_FRAME_WIDTH // 2))
        # First and last frames are the GIF's own first and last.
        self.assertLess(sheet.getpixel((5, 5))[0], 10)
        self.assertGreater(sheet.getpixel((sheet.width - 5, sheet.height - 5))[0], 30)

    def test_short_gifs_use_the_frames_they_have(self):
        self.assertEqual(Image.open(io.BytesIO(filmstrip(_gif_bytes(3)))).size, (3 * FILMSTRIP_FRAME_WIDTH, 80))
        self.assertEqual(Image.open(io.BytesIO(filmstrip(_gif_bytes(1)))).size, (FILMSTRIP_FRAME_WIDTH, 80))


class TestGiphyApi(unittest.IsolatedAsyncioTestCase):
    async def test_search_parses_gifs_and_skips_broken_ones(self):
        patcher, client = _api(data=[_item("abc123"), {"id": "broken", "images": {}}])
        with patcher:
            gifs = await search_gifs("key", "mic drop", offset=5)
        self.assertEqual([g.id for g in gifs], ["abc123"])
        self.assertEqual(gifs[0].preview_url, "https://media.giphy.com/fixed_width.gif")
        self.assertEqual(gifs[0].renditions[0], ("https://media.giphy.com/original.gif", 5000))
        self.assertEqual((gifs[0].width, gifs[0].height), (480, 270))
        params = client.get.await_args.kwargs["params"]
        self.assertEqual((params["api_key"], params["q"], params["offset"]), ("key", "mic drop", 5))

    async def test_errors_become_giphy_errors(self):
        for status in (403, 429, 500):
            patcher, _ = _api(status=status)
            with patcher, self.assertRaises(GiphyError):
                await search_gifs("key", "x")

    async def test_get_gif_handles_missing_and_malformed_ids(self):
        patcher, client = _api(status=404)
        with patcher:
            self.assertIsNone(await get_gif("key", "abc123"))
            self.assertIsNone(await get_gif("key", "../search"))
        client.get.assert_awaited_once()
        patcher, _ = _api(data=_item("abc123"))
        with patcher:
            self.assertEqual((await get_gif("key", "abc123")).id, "abc123")


class TestDownloads(unittest.IsolatedAsyncioTestCase):
    def _gif(self, *renditions):
        return Gif(id="abc123", title="t", url="u", width=1, height=1, renditions=renditions, preview_url="p")

    @patch("src.services.giphy_service.fetch_public", new_callable=AsyncMock)
    async def test_download_skips_renditions_that_are_too_big(self, fetch):
        request = httpx.Request("GET", "https://media.giphy.com/x.gif")
        fetch.side_effect = [ResponseTooLarge("big"), httpx.Response(200, content=b"GIF89a", request=request)]
        gif = self._gif(("huge", MAX_POST_BYTES + 1), ("lied", 10), ("small", 10))
        self.assertEqual(await download_gif(gif), b"GIF89a")
        self.assertEqual([call.args[0] for call in fetch.await_args_list], ["lied", "small"])

        with self.assertRaises(GiphyError):
            await download_gif(self._gif(("huge", MAX_POST_BYTES + 1)))

    @patch("src.services.giphy_service.fetch_public", new_callable=AsyncMock)
    async def test_filmstrip_failures_are_none(self, fetch):
        request = httpx.Request("GET", "https://media.giphy.com/x.gif")
        fetch.return_value = httpx.Response(200, content=b"not a gif", request=request)
        self.assertIsNone(await gif_filmstrip(self._gif(("u", 1))))
        fetch.return_value = httpx.Response(200, content=_gif_bytes(4), request=request)
        self.assertTrue((await gif_filmstrip(self._gif(("u", 1)))).startswith(b"\xff\xd8"))


if __name__ == "__main__":
    unittest.main()
