import json
import re
import time

import requests

GEN_BASE = "https://gen.pollinations.ai"
CLASSIC_TEXT = "https://text.pollinations.ai"

STYLES = {
    "Energetic": "fast-paced, punchy, upbeat, exciting",
    "Elegant": "refined, smooth, premium, sophisticated",
    "Funny": "playful, witty, lighthearted, memorable",
    "Luxury": "exclusive, cinematic, high-end, aspirational",
    "Minimal": "clean, direct, calm, confident",
    "Cinematic": "dramatic, filmic, immersive, epic",
}


class ScriptError(Exception):
    pass


_DASHES = dict.fromkeys(range(0x2010, 0x2016), "-")
_QUOTES = {0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"', 0x2026: "...", 0x00A0: " "}


def _clean(text: str) -> str:
    text = text.translate({**_DASHES, **_QUOTES})
    return " ".join(text.split())


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ScriptError(f"No JSON in LLM response: {text[:200]}")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        raise ScriptError(f"Invalid JSON in LLM response: {text[:200]}")


def _build_prompt(product, description, tagline, language, style, duration, has_reference=False) -> str:
    word_target = max(12, int(duration * 2.2))
    style_desc = STYLES.get(style, style)
    ref_note = (
        "The user will provide a reference photo of the exact product: describe the scene so that "
        "this exact product is the clear, sharp, central hero of every shot (match its colors, materials, shape). "
        if has_reference
        else f"The product ({product}) must be clearly visible as the sharp, central hero of every shot - "
        "never an empty room or generic scene. "
    )
    return (
        f"You are a commercial ad copywriter. Write assets for a {duration}-second video advertisement.\n"
        f"Product: {product}\n"
        f"Description: {description}\n"
        f"Tagline: {tagline or 'none'}\n"
        f"Tone: {style_desc}.\n"
        f'Reply ONLY with JSON: {{"voiceover": "...", "visual_prompt": "...", "title": "..."}}\n'
        f'- voiceover: one spoken line in {language}, about {word_target} words, natural for a voiceover, no emojis, no quotation marks.\n'
        f'- visual_prompt: a rich English description of the ad visuals (setting, product shots, camera moves, lighting) '
        f'suitable for a text-to-video AI model. {ref_note}'
        f'Start with a close-up or hero shot of the product itself.\n'
        f'- title: a short internal project title in {language}.'
    )


def _post_chat(base, messages, model, api_key=None, timeout=90):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {"model": model, "messages": messages, "temperature": 0.8}
    resp = requests.post(f"{base}/v1/chat/completions", json=payload, headers=headers, timeout=timeout)
    if resp.status_code == 402:
        wait = min(int(resp.headers.get("Retry-After", "30")), 45)
        time.sleep(wait)
        resp = requests.post(f"{base}/v1/chat/completions", json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _post_classic(messages, timeout=90):
    payload = {"messages": messages, "model": "openai-fast"}
    resp = requests.post(f"{CLASSIC_TEXT}/openai", json=payload, timeout=timeout)
    if resp.status_code == 402:
        wait = min(int(resp.headers.get("Retry-After", "30")), 45)
        time.sleep(wait)
        resp = requests.post(f"{CLASSIC_TEXT}/openai", json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def generate_script(product, description, tagline, language, style, duration, api_key="", has_reference=False):
    prompt = _build_prompt(product, description, tagline, language, style, duration, has_reference)
    messages = [{"role": "user", "content": prompt}]
    content = None
    errors = []
    if api_key:
        try:
            content = _post_chat(GEN_BASE, messages, "openai/gpt-5.4-nano", api_key)
        except Exception as exc:
            errors.append(f"keyed text: {exc}")
    if content is None:
        try:
            content = _post_classic(messages)
        except Exception as exc:
            errors.append(f"keyless text: {exc}")
    if content is None:
        raise ScriptError("All text backends failed: " + " | ".join(errors))
    data = _extract_json(content)
    voiceover = _clean((data.get("voiceover") or "").strip().strip('"'))
    visual = _clean((data.get("visual_prompt") or "").strip().strip('"'))
    title = _clean((data.get("title") or product).strip())
    if not voiceover or not visual:
        raise ScriptError(f"LLM returned empty fields: {data}")
    return {"voiceover": voiceover, "visual_prompt": visual, "title": title}
