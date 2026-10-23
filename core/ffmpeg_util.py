import re
import subprocess
import sys

import imageio_ffmpeg

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def ffmpeg_exe() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(args, timeout=600):
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-y", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").splitlines()[-12:])
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {tail}")
    return proc


def media_duration(path: str) -> float:
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
        creationflags=CREATE_NO_WINDOW,
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", proc.stderr or "")
    if not match:
        raise RuntimeError(f"Could not read duration of {path}")
    h, m, s = int(match.group(1)), int(match.group(2)), float(match.group(3))
    return h * 3600 + m * 60 + s


def escape_filter_path(path: str) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:")
