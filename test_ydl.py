import tempfile
import yt_dlp
import os

def _build_ydl_opts(file_prefix: str) -> dict:
    return {
        "format": "bwa/bestaudio/best[vcodec=h264]/best",
        "outtmpl": os.path.join(tempfile.gettempdir(), f"{file_prefix}_%(id)s.%(ext)s"),
        "quiet": False,
        "no_warnings": False,
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
        info = ydl.extract_info(url, download=True)
        audio_file = ydl.prepare_filename(info)
        print("Success! Audio file:", audio_file)
except Exception as e:
    print("Error:", e)
