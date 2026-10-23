import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import edge_tts

from core import ffmpeg_util, pipeline, tts, video_gen
from core.languages import LANGUAGES, voice_for


def check_analytics():
    from core import analytics, moods
    rec = analytics.recommend(
        "Aurora Watch", "premium minimalist wristwatch", "Time, elevated.")
    assert rec["category"] in {r[0] for r in analytics.CATEGORY_RULES} or rec["category"] == "general"
    assert 0 <= rec["score"]["score"] <= 100
    assert rec["mood"] in moods.MOOD_NAMES
    assert isinstance(rec["trend"], dict) and "status" in rec["trend"]
    scored = analytics.score_tagline("", "X", [])
    assert scored["score"] == 0
    assert moods.grade_for("Vibrant") and moods.grade_for("Auto (recommended)") == ""
    assert moods.prompt_for("Noir") and not moods.prompt_for("Auto (recommended)")
    print(f"OK analytics: category={rec['category']} score={rec['score']['score']} "
          f"mood={rec['mood']} suggestions={len(rec['suggestions'])}")


def check_voices():
    async def list_voices():
        return await edge_tts.list_voices()

    available = {v["ShortName"] for v in asyncio.run(list_voices())}
    missing = []
    for name, entry in LANGUAGES.items():
        for gender in ("male", "female"):
            if entry[gender] not in available:
                missing.append(f"{name}/{gender}: {entry[gender]}")
    if missing:
        print("MISSING VOICES:")
        for m in missing:
            print("  " + m)
        raise SystemExit(1)
    print(f"OK voices: all {len(LANGUAGES) * 2} voices available")


def check_ffmpeg():
    dur = ffmpeg_util.media_duration.__doc__
    from core.ffmpeg_util import ffmpeg_exe
    print(f"OK ffmpeg: {ffmpeg_exe()}")


def run_pipeline(args):
    work = Path(tempfile.mkdtemp(prefix="advideo_e2e_"))
    print(f"workdir: {work}")

    def log(msg):
        print("  " + msg)

    api_key = args.key or ""
    try:
        out = pipeline.generate_ad(
            product="Aurora Watch",
            description="A premium minimalist wristwatch with sapphire glass and 7-day battery, perfect for professionals.",
            tagline="Time, elevated.",
            language="English",
            gender="Female",
            style="Elegant",
            duration=8,
            aspect=args.aspect,
            model_key="auto",
            source_mode=args.source,
            captions=True,
            music=True,
            api_key=api_key,
            output_dir=str(work),
            log=log,
        )
    except Exception as exc:
        if "text" in str(exc).lower() or "json" in str(exc).lower() or "backend" in str(exc).lower():
            print(f"script generation unavailable ({exc}); retrying with canned script")
            out = _run_with_canned_script(args, work, api_key, log)
        else:
            raise
    dur = ffmpeg_util.media_duration(str(out))
    assert 6.5 <= dur <= 10.5, f"duration {dur}s out of range"
    proc = __import__("subprocess").run(
        [ffmpeg_util.ffmpeg_exe(), "-hide_banner", "-i", str(out)],
        capture_output=True, text=True,
    )
    assert "Audio:" in proc.stderr, "no audio stream in output"
    print(f"OK e2e: {out} ({dur:.2f}s, audio present)")


def _run_with_canned_script(args, work, api_key, log):
    from core import compose, tts as tts_mod, video_gen as vg

    log("1/5 canned script")
    voiceover = "Meet Aurora Watch. Sapphire glass, seven day battery, timeless design. Elevate every moment. Time, elevated."
    visual = "minimalist luxury watch on marble, soft studio light, slow cinematic orbit, golden reflections"
    aspect = "9:16" if args.aspect.startswith("9:16") else "16:9"
    log("2/5 video")
    video = vg.generate_video(visual, "auto", 8, aspect, api_key, work / "video.mp4", log,
                              args.source, product="Aurora Watch", tagline="Time, elevated.",
                              mood="Cinematic")
    log("3/5 voiceover")
    voice = voice_for("English", "Female")
    vo = tts_mod.fit_to_duration(voiceover, voice, work, 8.0, log)
    log("4/5 compose")
    out = work / f"canned_{args.aspect.replace(':', 'x')}.mp4"
    compose.compose(video, vo, out, 8.0, aspect, voiceover, "Time, elevated.", True, True, log)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--aspect", default="9:16", choices=["9:16", "16:9"])
    parser.add_argument("--source", default="fallback", choices=["auto", "fallback"])
    parser.add_argument("--key", default="")
    parser.add_argument("--skip-voices", action="store_true")
    args = parser.parse_args()
    if not args.skip_voices:
        check_voices()
    check_ffmpeg()
    check_analytics()
    run_pipeline(args)
    print("ALL OK")


if __name__ == "__main__":
    main()
