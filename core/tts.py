import asyncio
from pathlib import Path

import edge_tts

from .ffmpeg_util import media_duration, run_ffmpeg


class TTSError(Exception):
    pass


async def _synth(text, voice, out_path, rate):
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(str(out_path))


def synthesize(text: str, voice: str, out_path: Path) -> float:
    try:
        asyncio.run(_synth(text, voice, out_path, "+0%"))
    except Exception as exc:
        raise TTSError(f"edge-tts failed for voice {voice}: {exc}") from exc
    if not out_path.exists() or out_path.stat().st_size < 1_000:
        raise TTSError("edge-tts produced empty audio")
    return media_duration(out_path)


def fit_to_duration(text: str, voice: str, workdir: Path, target: float, log) -> Path:
    raw = workdir / "voiceover_raw.mp3"
    log(f"  voiceover ({voice})...")
    dur = synthesize(text, voice, raw)
    log(f"  voiceover length {dur:.1f}s (target {target:.1f}s)")
    if dur <= target * 0.97:
        return raw
    tempo = min(dur / target, 1.9)
    fitted = workdir / "voiceover.mp3"
    run_ffmpeg(
        ["-i", str(raw), "-filter:a", f"atempo={tempo:.4f}", "-b:a", "160k", str(fitted)],
        timeout=120,
    )
    new_dur = media_duration(fitted)
    log(f"  voiceover sped up x{tempo:.2f} -> {new_dur:.1f}s")
    return fitted
