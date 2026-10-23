import json
from pathlib import Path

from . import compose, languages, moods, projects, script_gen, tts, video_gen


def generate_ad(
    product: str,
    description: str,
    tagline: str,
    language: str,
    gender: str,
    style: str,
    duration: int,
    aspect: str,
    model_key: str,
    source_mode: str,
    captions: bool,
    music: bool,
    api_key: str,
    output_dir: str,
    log=print,
    reference_image: str = "",
    mood: str = "Auto (recommended)",
    project_dir: str = "",
) -> Path:
    if not product.strip():
        raise ValueError("Product name is required")
    if not description.strip():
        raise ValueError("Description is required")
    output = Path(output_dir).expanduser()
    output.mkdir(parents=True, exist_ok=True)
    aspect_code = "9:16" if aspect.startswith("9:16") else "16:9"

    if project_dir:
        pdir = Path(project_dir)
        pdata = projects.load(pdir)
        pdata["status"] = "running"
        pdata["error"] = ""
        projects.save(pdir, pdata)
    else:
        params = dict(product=product, description=description, tagline=tagline,
                      language=language, gender=gender, style=style, duration=duration,
                      aspect=aspect_code, model_key=model_key, source_mode=source_mode,
                      captions=captions, music=music, reference_image=reference_image,
                      mood=mood)
        pdata = projects.create(output, params, reference_image)
        pdir = Path(pdata["dir"])
        log(f"   project folder: {pdir.name}")

    ref = pdata["params"].get("reference_image", "") or reference_image
    has_ref = bool(ref) and Path(ref).exists()
    if ref and not has_ref:
        log(f"   warning: reference image not found ({ref}), ignoring")
    workdir = pdir / "work"
    workdir.mkdir(exist_ok=True)
    script_file = workdir / "script.json"
    video_path = workdir / "video.mp4"
    voice_meta = workdir / "voice.json"
    stages = pdata.get("stages", {})

    try:
        if stages.get("script") and projects.is_usable_file(script_file, 50):
            script = json.loads(script_file.read_text(encoding="utf-8"))
            log("1/5 Script ready (resumed)")
        else:
            log(f"1/5 Generating ad script ({language}, {style}, mood: {moods.mood_key(mood)})...")
            script = script_gen.generate_script(
                product, description, tagline, language, style, duration, api_key, has_ref
            )
            script_file.write_text(json.dumps(script, indent=2, ensure_ascii=False),
                                   encoding="utf-8")
            log(f'   voiceover: "{script["voiceover"]}"')
            log(f"   visual: {script['visual_prompt'][:100]}...")
            projects.mark_stage(pdir, pdata, "script")

        if stages.get("visuals") and projects.is_usable_file(video_path, 10_000):
            log("2/5 Visuals ready (resumed)")
        else:
            log("2/5 Generating video visuals...")
            video_gen.generate_video(
                script["visual_prompt"], model_key, duration, aspect_code,
                api_key, video_path, log, source_mode,
                reference_image=ref if has_ref else None,
                product=product, tagline=tagline, mood=mood,
            )
            projects.mark_stage(pdir, pdata, "visuals")

        if stages.get("voice"):
            vmeta = {}
            if projects.is_usable_file(voice_meta, 2):
                try:
                    vmeta = json.loads(voice_meta.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    vmeta = {}
            vpath = workdir / vmeta.get("file", "voiceover_raw.mp3")
            if projects.is_usable_file(vpath, 1_000):
                voice_path = vpath
                log("3/5 Voiceover ready (resumed)")
            else:
                voice_path = _make_voice(script, language, gender, duration, workdir, log)
                projects.mark_stage(pdir, pdata, "voice")
        else:
            voice_path = _make_voice(script, language, gender, duration, workdir, log)
            projects.mark_stage(pdir, pdata, "voice")

        slug = projects.slugify(product)
        out_path = pdir / f"{slug}_{duration}s_{aspect_code.replace(':', 'x')}.mp4"
        if stages.get("compose") and projects.is_usable_file(out_path, 50_000):
            log("4/5 Final video ready (resumed)")
        else:
            log("4/5 Assembling final video...")
            compose.compose(
                video_path=video_path,
                voiceover_path=voice_path,
                out_path=out_path,
                duration=float(duration),
                aspect=aspect_code,
                voiceover_text=script["voiceover"],
                tagline=tagline,
                music=music,
                captions=captions,
                log=log,
                mood=mood,
            )
            projects.mark_stage(pdir, pdata, "compose")

        projects.set_status(pdir, pdata, "completed", video=str(out_path))
        log(f"5/5 Done: {out_path}")
        return out_path
    except Exception as exc:
        try:
            projects.set_status(pdir, pdata, "failed", error=str(exc)[:300])
        except Exception:
            pass
        raise


def _make_voice(script, language, gender, duration, workdir: Path, log) -> Path:
    log("3/5 Generating voiceover...")
    voice = languages.voice_for(language, gender)
    path = tts.fit_to_duration(script["voiceover"], voice, workdir, float(duration), log)
    try:
        (workdir / "voice.json").write_text(json.dumps({"file": path.name}), encoding="utf-8")
    except OSError:
        pass
    return path
