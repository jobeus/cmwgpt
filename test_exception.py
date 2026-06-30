import tempfile
import yt_dlp
import os

def _build_ydl_opts(file_prefix: str) -> dict:
    return {
        "format": "best", # forces bytevc1 without audio
        "outtmpl": os.path.join(tempfile.gettempdir(), f"{file_prefix}_%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "64",
        }],
    }

opts = _build_ydl_opts("test")
url = "https://vt.tiktok.com/ZSCYW5BNt/"
try:
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)
except Exception as e:
    print("Caught exception type:", type(e))
    print("Exception string:", str(e))
    print("Is it PostProcessingError?", isinstance(e, yt_dlp.utils.PostProcessingError))
    print("Is it DownloadError?", isinstance(e, yt_dlp.utils.DownloadError))
