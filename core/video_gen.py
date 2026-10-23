import hashlib
import json
import time
import urllib.parse
from pathlib import Path

import requests
from PIL import Image

from .ffmpeg_util import run_ffmpeg

GEN_BASE = "https://gen.pollinations.ai"
CLASSIC_IMAGE = "https://image.pollinations.ai/prompt"
MEDIA_UPLOAD = "https://media.pollinations.ai/upload"

VIDEO_MODELS = {
    "auto": {"id": "bytedance/seedance-2.0-mini", "durations": list(range(4, 11)), "resolution": "720p"},
    "bytedance/seedance-2.0-mini": {"id": "bytedance/seedance-2.0-mini", "durations": list(range(4, 11)), "resolution": "720p"},
    "alibaba/wan-2.6": {"id": "alibaba/wan-2.6", "durations": [5, 10, 15], "resolution": "720p"},
    "google/veo-3.1-fast": {"id": "google/veo-3.1-fast", "durations": [4, 6, 8], "resolution": "720p"},
}

MODEL_LABELS = [
    "auto",
    "bytedance/seedance-2.0-mini",
    "alibaba/wan-2.6",
    "google/veo-3.1-fast",
]

SUPPORTED_REF_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


class VideoError(Exception):
    pass


def snap_duration(model_key: str, duration: int) -> int:
    allowed = VIDEO_MODELS[model_key]["durations"]
    return min(allowed, key=lambda d: (abs(d - duration), d))


def _headers(api_key):
    h = {}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


def _download(url, dest: Path, api_key=""):
    with requests.get(url, stream=True, timeout=300, headers=_headers(api_key)) as resp:
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "")
        if "json" in ctype:
            raise VideoError(f"Expected video, got JSON from {url}: {resp.text[:200]}")
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                fh.write(chunk)
    if dest.stat().st_size < 10_000:
        raise VideoError(f"Downloaded video is suspiciously small: {dest.stat().st_size} bytes")


def _aspect_size(aspect: str):
    return (576, 1024) if aspect == "9:16" else (1024, 576)


def _upload_file(file_path: Path, api_key: str, log):
    with open(file_path, "rb") as fh:
        resp = requests.post(
            MEDIA_UPLOAD,
            headers={**_headers(api_key), "X-File-Name": file_path.name, "Content-Type": "image/jpeg"},
            data=fh.read(),
            timeout=120,
        )
    if not resp.ok:
        raise VideoError(f"upload failed HTTP {resp.status_code}")
    link = resp.headers.get("Link", "")
    url = link.strip("<>").split(";")[0] if link else ""
    if not url:
        url = (resp.json() or {}).get("url", "")
    if not url:
        raise VideoError("upload returned no URL")
    return url


def _generate_start_image(prompt: str, aspect: str, api_key: str, dest: Path, log):
    w, h = _aspect_size(aspect)
    if api_key:
        url = f"{GEN_BASE}/image/{urllib.parse.quote(prompt)}"
        params = {"model": "black-forest-labs/flux.1-schnell", "width": w * 2, "height": h * 2, "key": api_key}
        resp = requests.get(url, params=params, headers=_headers(api_key), timeout=180)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    else:
        url = f"{CLASSIC_IMAGE}/{urllib.parse.quote(prompt)}"
        resp = requests.get(url, params={"width": w, "height": h, "safe": "true"}, timeout=180)
        if resp.status_code == 402:
            wait = min(int(resp.headers.get("Retry-After", "45")), 60)
            log(f"  free image slot busy - waiting {wait}s...")
            time.sleep(wait)
            resp = requests.get(url, params={"width": w, "height": h, "safe": "true"}, timeout=180)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    if dest.stat().st_size < 5_000:
        raise VideoError("Start image generation failed (empty file)")
    return dest


def _submit_video_job(prompt, model_key, duration, api_key, image_url=None):
    model = VIDEO_MODELS[model_key]
    params = {"model": model["id"], "duration": duration, "resolution": model["resolution"]}
    if image_url:
        params["image"] = image_url
    url = f"{GEN_BASE}/video/{urllib.parse.quote(prompt)}?" + urllib.parse.urlencode(params)
    headers = {**_headers(api_key), "Prefer": "respond-async"}
    return requests.get(url, headers=headers, timeout=120)


def _poll_job(job_id, api_key, log, timeout_s=420):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        resp = requests.get(f"{GEN_BASE}/v1/jobs/{job_id}", headers=_headers(api_key), timeout=60)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status")
        if status == "succeeded":
            return data.get("result_url") or f"{GEN_BASE}/v1/jobs/{job_id}/result"
        if status == "failed":
            raise VideoError(f"Video job failed: {data.get('error') or data}")
        log(f"  video job {status or 'pending'}...")
        time.sleep(6)
    raise VideoError("Video job timed out after 7 minutes")


def generate_ai_video(visual_prompt, model_key, duration, api_key, out_path: Path, log,
                      reference_image=None, aspect="9:16"):
    model_key = model_key if model_key in VIDEO_MODELS else "auto"
    snapped = snap_duration(model_key, duration)
    log(f"  AI video: model={VIDEO_MODELS[model_key]['id']} duration={snapped}s")
    image_url = None
    try:
        if reference_image and Path(reference_image).exists():
            log("  uploading your reference image...")
            image_url = _upload_file(Path(reference_image), api_key, log)
        else:
            img_path = out_path.with_name(out_path.stem + "_start.jpg")
            _generate_start_image(visual_prompt, aspect, api_key, img_path, log)
            image_url = _upload_file(img_path, api_key, log)
        log(f"  start frame ready: {image_url}")
    except Exception as exc:
        log(f"  start frame skipped ({exc}), using text-to-video")
    try:
        resp = _submit_video_job(visual_prompt, model_key, snapped, api_key, image_url)
        if resp.status_code >= 400:
            if image_url:
                log(f"  image-to-video rejected ({resp.status_code}), retrying text-to-video")
                resp = _submit_video_job(visual_prompt, model_key, snapped, api_key, None)
            resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "")
        if "video" in ctype or "octet-stream" in ctype:
            out_path.write_bytes(resp.content)
        else:
            job = resp.json()
            job_id = job.get("id") or resp.headers.get("Location", "").rstrip("/").split("/")[-1]
            if not job_id:
                raise VideoError(f"No job id in response: {resp.text[:200]}")
            log(f"  video job submitted: {job_id}")
            result_url = _poll_job(job_id, api_key, log)
            _download(result_url, out_path, api_key)
        log(f"  AI video ready ({out_path.stat().st_size // 1024} KB)")
        return out_path
    except Exception:
        if out_path.exists():
            out_path.unlink()
        raise


def generate_keyless_video(visual_prompt, model_key, duration, aspect, out_path: Path, log,
                           timeout_s=90):
    model_key = model_key if model_key in VIDEO_MODELS else "auto"
    snapped = snap_duration(model_key, duration)
    log("  trying free AI video (no key)...")
    resp = _submit_video_job(visual_prompt, model_key, snapped, "", None)
    if resp.status_code in (401, 402, 403):
        raise VideoError(f"free video endpoint returned HTTP {resp.status_code}")
    resp.raise_for_status()
    ctype = resp.headers.get("Content-Type", "")
    if "video" in ctype or "octet-stream" in ctype:
        out_path.write_bytes(resp.content)
    else:
        job = resp.json()
        job_id = job.get("id") or resp.headers.get("Location", "").rstrip("/").split("/")[-1]
        if not job_id:
            raise VideoError(f"No job id in response: {resp.text[:200]}")
        log(f"  free video job submitted: {job_id}")
        result_url = _poll_job(job_id, "", log, timeout_s=timeout_s)
        _download(result_url, out_path, "")
    if not out_path.exists() or out_path.stat().st_size < 10_000:
        raise VideoError("free video result was empty")
    log(f"  free AI video ready ({out_path.stat().st_size // 1024} KB)")
    return out_path


def _font(size, bold=False):
    from PIL import ImageFont
    for name in ("arialbd.ttf" if bold else "arial.ttf", "segoeuib.ttf" if bold else "segoeui.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.truetype(f"C:/Windows/Fonts/{'arialbd.ttf' if bold else 'arial.ttf'}", size)
    except OSError:
        return ImageFont.load_default()


def render_endcard(product: str, tagline: str, aspect: str, mood: str, dest: Path):
    from PIL import Image, ImageDraw

    from . import moods
    w, h = (1080, 1920) if aspect == "9:16" else (1920, 1080)
    top, bottom, accent = moods.endcard_colors(mood)

    def hx(c):
        c = c.lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))

    t, b = hx(top), hx(bottom)
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        k = y / max(h - 1, 1)
        draw.line([(0, y), (w, y)], fill=tuple(int(t[i] + (b[i] - t[i]) * k) for i in range(3)))
    bar_w = int(w * 0.16)
    bar_h = max(6, h // 160)
    draw.rounded_rectangle(
        [(w // 2 - bar_w // 2, int(h * 0.38)), (w // 2 + bar_w // 2, int(h * 0.38) + bar_h)],
        radius=bar_h // 2, fill=hx(accent))

    def wrap(text, fnt, max_w):
        words, lines, cur = text.split(), [], ""
        probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        for word in words:
            trial = f"{cur} {word}".strip()
            if probe.textbbox((0, 0), trial, font=fnt)[2] <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        return lines

    max_w = int(w * 0.84)
    name_font = _font(int(h * (0.052 if len(product) < 22 else 0.04)), bold=True)
    tag_font = _font(int(h * 0.032))
    y = int(h * 0.44)
    for line in wrap(product, name_font, max_w):
        bbox = draw.textbbox((0, 0), line, font=name_font)
        draw.text(((w - (bbox[2] - bbox[0])) // 2 - bbox[0], y), line, font=name_font,
                  fill="#FFFFFF")
        y += int(h * 0.062)
    y += int(h * 0.02)
    if tagline:
        for line in wrap(tagline, tag_font, max_w):
            bbox = draw.textbbox((0, 0), line, font=tag_font)
            draw.text(((w - (bbox[2] - bbox[0])) // 2 - bbox[0], y), line, font=tag_font,
                      fill=hx(accent))
            y += int(h * 0.042)
    img.save(dest, quality=95)
    return dest


MOVES = [
    # z0, z1, x0, x1, y0, y1
    (1.00, 1.14, 0.50, 0.50, 0.50, 0.42),   # 0 push-in
    (1.18, 1.05, 0.15, 0.85, 0.35, 0.65),   # 1 pan L->R
    (1.10, 1.00, 0.50, 0.50, 0.45, 0.55),   # 2 pull-out
    (1.02, 1.16, 0.20, 0.50, 0.75, 0.45),   # 3 push-in from corner
    (1.06, 1.20, 0.80, 0.20, 0.20, 0.80),   # 4 diagonal drift
    (1.15, 1.02, 0.85, 0.15, 0.50, 0.50),   # 5 reveal pan R->L
]

TRANSITIONS = [
    ("slideleft", 0.30),
    ("fade", 0.26),
    ("wipeleft", 0.30),
    ("smoothleft", 0.32),
    ("dissolve", 0.28),
    ("circleopen", 0.34),
]


def _hash_text(text: str) -> str:
    return hashlib.md5(text.encode("utf-8", errors="ignore")).hexdigest()[:12]


def _scene_clip(image_path: Path, out: Path, aspect: str, frames: int, move: int, log,
                ease: float = 1.0):
    w, h = (1080, 1920) if aspect == "9:16" else (1920, 1080)
    big_w, big_h = w * 3, h * 3
    workdir = out.parent
    prepared = _prepare_base(image_path, aspect, big_w, big_h, workdir,
                             name=f"base_{out.stem}.jpg")
    args = MOVES[move % len(MOVES)]
    _clip(prepared, out, big_w, big_h, w, h, frames, *args, log, ease=ease)


def _prepare_base(image_path: Path, aspect: str, big_w: int, big_h: int, workdir: Path,
                  name: str = "base_prepared.jpg") -> Path:
    img = Image.open(image_path)
    if img.mode != "RGB":
        bg = Image.new("RGB", img.size, (12, 12, 16))
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1])
        else:
            bg.paste(img.convert("RGB"))
        img = bg
    target_w, target_h = (576, 1024) if aspect == "9:16" else (1024, 576)
    scale = max(target_w / img.width, target_h / img.height)
    new_size = (round(img.width * scale * 1.35), round(img.height * scale * 1.35))
    img = img.resize(new_size, Image.LANCZOS)
    cx, cy = img.width // 2, img.height // 2
    half_w, half_h = target_w // 2, target_h // 2
    img = img.crop((cx - half_w, cy - half_h, cx + half_w, cy + half_h))
    prepared = workdir / name
    img.save(prepared, quality=95)
    return prepared


def _clip(prepared: Path, out: Path, big_w: int, big_h: int, w: int, h: int,
          frames: int, z0: float, z1: float, x0: float, x1: float, y0: float, y1: float, log,
          ease: float = 1.0):
    fps = 30
    steps = max(frames - 1, 1)
    p = f"pow(on/{steps},{ease:.3f})"
    z_in = f"{z0:.5f}+({z1:.5f}-{z0:.5f})*{p}"
    x_in = f"({x0:.5f}+({x1:.5f}-{x0:.5f})*{p})*(iw-iw/zoom)"
    y_in = f"({y0:.5f}+({y1:.5f}-{y0:.5f})*{p})*(ih-ih/zoom)"
    vf = (
        f"scale={big_w}:{big_h}:flags=lanczos,"
        f"zoompan=z='min(max({z_in},1.0),1.35)':x='{x_in}':y='{y_in}'"
        f":d=1:s={w}x{h}:fps={fps}"
    )
    run_ffmpeg(
        [
            "-loop", "1",
            "-i", str(prepared),
            "-vf", vf,
            "-frames:v", str(frames),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            str(out),
        ],
        timeout=600,
    )


def _ken_burns(image_path: Path, video_path: Path, duration: int, aspect: str, log):
    w, h = (1080, 1920) if aspect == "9:16" else (1920, 1080)
    big_w, big_h = w * 3, h * 3
    fps = 30
    fade = min(0.8, duration * 0.12)
    shot_frames = int((duration + fade) / 2 * fps)
    workdir = video_path.parent
    prepared = _prepare_base(image_path, aspect, big_w, big_h, workdir)
    shot_a = workdir / "shot_a.mp4"
    shot_b = workdir / "shot_b.mp4"
    log("  shot 1/2: slow push-in...")
    _clip(prepared, shot_a, big_w, big_h, w, h, shot_frames, 1.0, 1.14, 0.5, 0.5, 0.5, 0.42, log)
    log("  shot 2/2: cinematic pan...")
    _clip(prepared, shot_b, big_w, big_h, w, h, shot_frames, 1.18, 1.05, 0.15, 0.85, 0.35, 0.65, log)
    offset = shot_frames / fps - fade
    run_ffmpeg(
        [
            "-i", str(shot_a),
            "-i", str(shot_b),
            "-filter_complex",
            f"[0:v][1:v]xfade=transition=fade:duration={fade:.2f}:offset={offset:.2f},format=yuv420p[v]",
            "-map", "[v]",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
            str(video_path),
        ],
        timeout=600,
    )
    log(f"  fallback video ready ({video_path.stat().st_size // 1024} KB)")
    return video_path


def _image_cached_or_gen(prompt: str, aspect: str, api_key: str, dest: Path, log):
    ph = dest.with_name(dest.name + ".ph")
    want = _hash_text(prompt)
    try:
        if (dest.exists() and dest.stat().st_size > 5_000 and ph.exists()
                and ph.read_text(encoding="utf-8").strip() == want):
            log(f"  {dest.stem} image reused (cached)")
            return dest
    except OSError:
        pass
    _generate_start_image(prompt, aspect, api_key, dest, log)
    try:
        ph.write_text(want, encoding="utf-8")
    except OSError:
        pass
    return dest


def generate_storyboard_video(visual_prompt, duration, aspect, out_path: Path, api_key, log,
                              reference_image=None, product="", tagline="", mood="Auto (recommended)",
                              model_key="auto"):
    from . import moods
    workdir = out_path.parent
    fps = 30
    fade = min(0.7, duration * 0.1)
    endcard_dur = 2.2 if tagline else 1.4

    scene_imgs = []
    if reference_image and Path(reference_image).exists():
        log(f"  scene 1: your reference image ({Path(reference_image).name})")
        scene_imgs.append(Path(reference_image))
    else:
        hero_prompt = visual_prompt
        if product:
            hero_prompt = (f"Hero product shot of {product}: {visual_prompt}. "
                           f"Product is the sharp central focus, studio advertising photography")
        if moods.prompt_for(mood):
            hero_prompt += f", {moods.prompt_for(mood)}"
        if tagline:
            hero_prompt += ", no text, no words, no letters"
        hero_img = workdir / (out_path.stem + "_hero.jpg")
        log("  scene 1: generating hero shot from your description...")
        try:
            _image_cached_or_gen(hero_prompt, aspect, api_key, hero_img, log)
            scene_imgs.insert(0, hero_img)
        except Exception as exc:
            log(f"  hero image failed ({exc})")

    if len(scene_imgs) < 2 and product:
        ctx_prompt = (f"Lifestyle advertising scene featuring {product}: {visual_prompt[:300]}. "
                      f"Wider context shot, environment, mood")
        if moods.prompt_for(mood):
            ctx_prompt += f", {moods.prompt_for(mood)}"
        ctx_img = workdir / (out_path.stem + "_ctx.jpg")
        log("  scene 2: generating AI context scene from description + tagline...")
        try:
            _image_cached_or_gen(ctx_prompt, aspect, api_key, ctx_img, log)
            scene_imgs.append(ctx_img)
        except Exception as exc:
            log(f"  context image failed ({exc})")

    if len(scene_imgs) >= 2 and duration >= 8:
        detail_prompt = (f"Extreme macro close-up of {product or 'the product'}: texture, "
                         f"material, craftsmanship detail, shallow depth of field product photography")
        if moods.prompt_for(mood):
            detail_prompt += f", {moods.prompt_for(mood)}"
        detail_prompt += ", no text, no words, no letters"
        detail_img = workdir / (out_path.stem + "_detail.jpg")
        log("  scene 3: generating macro detail shot...")
        try:
            _image_cached_or_gen(detail_prompt, aspect, api_key, detail_img, log)
            scene_imgs.append(detail_img)
        except Exception as exc:
            log(f"  detail image failed ({exc})")

    endcard = None
    if tagline or product:
        endcard = workdir / (out_path.stem + "_end.jpg")
        log("  scene: rendering tagline end card..." if tagline
            else "  scene: rendering product end card...")
        render_endcard(product or "", tagline, aspect, mood, endcard)

    if not scene_imgs and not endcard:
        raise VideoError("could not build any scene for the video")

    n = len(scene_imgs)
    scene_dur = max(1.8, (duration - endcard_dur + n * fade) / max(n, 1))
    frames_scene = max(int(scene_dur * fps), int(2.25 * fps))
    frames_end = int(endcard_dur * fps)

    sub_plans = []  # (clip, frames, move, ease, image)
    if scene_imgs:
        frames_a = frames_scene // 2
        frames_b = frames_scene - frames_a
        for i in range(n):
            ease_a = 1.35 if i % 2 == 0 else 0.8
            ease_b = 0.8 if i % 2 == 0 else 1.35
            sub_plans.append((workdir / f"sb_{i}a.mp4", frames_a,
                              (2 * i) % len(MOVES), ease_a, scene_imgs[i]))
            sub_plans.append((workdir / f"sb_{i}b.mp4", frames_b,
                              (2 * i + 1) % len(MOVES), ease_b, scene_imgs[i]))
    if endcard:
        sub_plans.append((workdir / "sb_end.mp4", frames_end, 2, 0.85, endcard))

    recipe = {
        "v": 3, "aspect": aspect, "fs": frames_scene, "fe": frames_end, "n": n,
        "imgs": [(p.name, _hash_text(p.name + str(int(p.stat().st_mtime))))
                 for p in scene_imgs],
    }
    recipe_s = json.dumps(recipe, sort_keys=True)
    meta_path = workdir / (out_path.stem + "_sbmeta.json")
    try:
        old = meta_path.read_text(encoding="utf-8") if meta_path.exists() else ""
    except OSError:
        old = ""
    reuse = bool(old) and old.strip() == recipe_s and all(
        c.exists() and c.stat().st_size > 2_000 for c, *_ in sub_plans)
    if reuse:
        log(f"  {len(sub_plans)} scene shots reused (cached)")
    else:
        total = len(sub_plans)
        for i, (clip, frames, move, ease, img) in enumerate(sub_plans):
            log(f"  animating shot {i + 1}/{total}...")
            _scene_clip(img, clip, aspect, frames, move, log, ease=ease)
        try:
            meta_path.write_text(recipe_s, encoding="utf-8")
        except OSError:
            pass

    clips = [c for c, *_ in sub_plans]
    if len(clips) == 1:
        clips[0].replace(out_path)
        log(f"  storyboard video ready ({out_path.stat().st_size // 1024} KB)")
        return out_path

    durs = [frames / fps for _, frames, _, _, _ in sub_plans]
    cmd = []
    for c in clips:
        cmd += ["-i", str(c)]
    filters = []
    prev_label = "[0:v]"
    acc = durs[0]
    for i in range(1, len(clips)):
        if i == len(clips) - 1 and endcard:
            trans, tdur = ("fade", 0.45)
        else:
            trans, tdur = TRANSITIONS[(i - 1) % len(TRANSITIONS)]
        offset = max(0.0, acc - tdur)
        out_label = "[x%d]" % i if i < len(clips) - 1 else "[v]"
        filters.append(
            f"{prev_label}[{i}:v]xfade=transition={trans}:duration={tdur:.2f}"
            f":offset={offset:.2f}{out_label}")
        prev_label = out_label
        acc = offset + durs[i]
    filters.append(f"{prev_label}format=yuv420p[vout]")
    run_ffmpeg(
        cmd + [
            "-filter_complex", ";".join(filters),
            "-map", "[vout]",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
            str(out_path),
        ],
        timeout=900,
    )
    log(f"  storyboard video ready ({out_path.stat().st_size // 1024} KB, {len(clips)} shots)")
    return out_path


def generate_video(visual_prompt, model_key, duration, aspect, api_key, out_path: Path, log,
                   source_mode="auto", reference_image=None, product="", tagline="",
                   mood="Auto (recommended)"):
    storyboard = dict(visual_prompt=visual_prompt, duration=duration, aspect=aspect,
                      out_path=out_path, api_key=api_key, log=log,
                      reference_image=reference_image, product=product, tagline=tagline,
                      mood=mood, model_key=model_key)
    if source_mode == "fallback":
        return generate_storyboard_video(**storyboard)
    if not api_key:
        log("  no API key -> trying free AI video, then AI storyboard...")
        try:
            return generate_keyless_video(visual_prompt, model_key, duration, aspect,
                                          out_path, log)
        except Exception as exc:
            log(f"  free AI video unavailable ({exc}); building AI storyboard instead")
            return generate_storyboard_video(**storyboard)
    try:
        return generate_ai_video(visual_prompt, model_key, duration, api_key, out_path, log,
                                 reference_image, aspect)
    except Exception as exc:
        log(f"  AI video failed ({exc}); building AI storyboard")
        return generate_storyboard_video(**storyboard)
