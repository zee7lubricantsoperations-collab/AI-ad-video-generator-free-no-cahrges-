import math
import struct
import wave
from pathlib import Path

from . import moods
from .ffmpeg_util import escape_filter_path, media_duration, run_ffmpeg

ASPECT_SIZES = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
}


class ComposeError(Exception):
    pass


BRIGHT_CHORDS = [
    (220.00, 261.63, 329.63),
    (174.61, 220.00, 261.63),
    (261.63, 329.63, 392.00),
    (196.00, 246.94, 293.66),
]

DARK_CHORDS = [
    (220.00, 261.63, 311.13),
    (174.61, 207.65, 261.63),
    (196.00, 233.08, 293.66),
    (164.81, 207.65, 246.94),
]

BRIGHT_ARP = [
    (220.00, 329.63, 440.00),
    (174.61, 261.63, 349.23),
    (261.63, 392.00, 523.25),
    (196.00, 293.66, 392.00),
]


def _music_profile(mood: str):
    key = moods.mood_key(mood)
    bright = moods.is_bright(key)
    if key in ("Vibrant",):
        return BRIGHT_ARP, 1.0, 0.9
    if key in ("Noir", "Cool"):
        return DARK_CHORDS, 0.55, 1.15
    if key in ("Vintage", "Warm"):
        return BRIGHT_CHORDS, 0.7, 1.08
    if key in ("Cinematic",):
        return DARK_CHORDS, 0.75, 1.0
    return (BRIGHT_CHORDS if bright else DARK_CHORDS), 1.0, 1.0


def _write_music_pad(path: Path, duration: int, mood: str = "Auto (recommended)", sample_rate=22050):
    chords, tempo, brightness = _music_profile(mood)
    total = duration
    n_total = int(total * sample_rate)
    frames = bytearray()
    chord_len = total / len(chords) / tempo if tempo else total / len(chords)
    cycles = max(1, int(math.ceil((total / chord_len) / len(chords))))
    seq = chords * cycles
    for i in range(n_total):
        t = i / sample_rate
        ci = min(int(t / chord_len), len(seq) - 1)
        chord = seq[ci]
        ct = t - ci * chord_len
        env = min(ct / 0.35, 1.0) * max(0.0, 1.0 - max(0.0, ct - (chord_len - 0.5)) / 0.5)
        s = 0.0
        for f in chord:
            s += math.sin(2 * math.pi * f * t) * 0.33
            s += math.sin(2 * math.pi * f * 2 * t) * 0.08
        sample = max(-1.0, min(1.0, s * env * 0.35 * brightness))
        frames += struct.pack("<h", int(sample * 32767))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(frames))


def _wrap_text(text: str, width=32) -> str:
    words = text.split()
    lines, current = [], ""
    for word in words:
        if len(current) + len(word) + 1 > width and current:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return "\n".join(lines)


def _write_ass(path: Path, voiceover: str, tagline: str, duration: float, height: int):
    fontsize = max(36, int(height * 0.042))
    margin_v = int(height * 0.12)
    wrap_w = 34 if height > width_placeholder(height) else 40

    def event(text, start, end, style="Default"):
        return f"Dialogue: 0,{start},{end},{style},,0,0,0,,{_wrap_text(text, wrap_w)}"

    def ts(sec):
        sec = max(0.0, sec)
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = sec % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    events = []
    if tagline and duration > 3.5:
        events.append(event(voiceover, ts(0.2), ts(duration - 2.0)))
        events.append(event(tagline, ts(duration - 1.9), ts(duration - 0.1), "Tagline"))
    else:
        events.append(event(voiceover, ts(0.2), ts(duration - 0.1)))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: {height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{fontsize},&H00FFFFFF,&H00FFFFFF,&H00141414,&H96000000,-1,0,0,0,100,100,0,0,1,3,1,2,80,80,{margin_v},1
Style: Tagline,Arial,{int(fontsize * 1.25)},&H00FFFFFF,&H00FFFFFF,&H00141414,&H96000000,-1,0,0,0,100,100,0,0,1,3,1,2,80,80,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def width_placeholder(height: int) -> int:
    return 1080 if height == 1920 else 1920


def compose(
    video_path: Path,
    voiceover_path: Path,
    out_path: Path,
    duration: float,
    aspect: str,
    voiceover_text: str,
    tagline: str = "",
    music: bool = True,
    captions: bool = True,
    log=print,
    mood: str = "Auto (recommended)",
):
    w, h = ASPECT_SIZES[aspect]
    workdir = out_path.parent
    inputs = ["-stream_loop", "-1", "-i", str(video_path), "-i", str(voiceover_path)]
    vo_dur = media_duration(voiceover_path)
    if vo_dur > duration + 0.15:
        log(f"  warning: voiceover ({vo_dur:.1f}s) longer than video ({duration:.1f}s)")
    vf_parts = [
        f"scale={w}:{h}:force_original_aspect_ratio=decrease",
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
        "fps=30",
    ]
    grade = moods.grade_for(mood)
    if grade:
        vf_parts.append(grade)
        log(f"  mood filter: {moods.mood_key(mood)}")
    if captions and voiceover_text.strip():
        ass_path = workdir / "captions.ass"
        _write_ass(ass_path, voiceover_text, tagline or "", duration, h)
        vf_parts.append(f"subtitles='{escape_filter_path(ass_path)}'")
    video_filter = ",".join(vf_parts)
    if music:
        music_path = workdir / "music.wav"
        _write_music_pad(music_path, int(math.ceil(duration)) + 1, mood)
        inputs += ["-i", str(music_path)]
        graph = (
            f"[0:v]{video_filter}[v];"
            f"[2:a]volume=0.22,afade=t=out:st={duration - 0.8:.2f}:d=0.8[bg];"
            f"[1:a]apad=whole_dur={duration:.2f}[vo];"
            f"[vo][bg]amix=inputs=2:duration=first:dropout_transition=0[a]"
        )
    else:
        graph = f"[0:v]{video_filter}[v];[1:a]apad=whole_dur={duration:.2f}[a]"
    log("  encoding final MP4...")
    run_ffmpeg(
        [
            *inputs,
            "-filter_complex", graph,
            "-map", "[v]",
            "-map", "[a]",
            "-t", f"{duration:.2f}",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(out_path),
        ],
        timeout=900,
    )
    final = media_duration(out_path)
    log(f"  final video: {final:.1f}s {w}x{h} ({out_path.stat().st_size // 1024} KB)")
    return out_path
