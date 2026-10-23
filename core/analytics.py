import time

import requests

GOOGLE_SUGGEST = "https://suggestqueries.google.com/complete/search"
DDG_AC = "https://duckduckgo.com/ac/"
WIKI_SEARCH = "https://en.wikipedia.org/w/api.php"
WIKI_PAGEVIEWS = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{title}/daily/{start}/{end}"

HEADERS = {"User-Agent": "AIAdVideoMaker/1.0 (tagline analytics)"}

POWER_WORDS = {
    "free", "new", "best", "ultimate", "instant", "pro", "now", "today", "secret",
    "boost", "shine", "discover", "love", "feel", "transform", "glow", "power",
    "boost", "fast", "easy", "save", "win", "perfect", "guaranteed", "exclusive",
}

CATEGORY_RULES = [
    ("beauty", ["skin", "serum", "glow", "hair", "beauty", "cream", "makeup",
                "cosmetic", "face", "moistur", "wrinkle", "acne"], "Vibrant", "Elegant"),
    ("food & drink", ["coffee", "tea", "snack", "food", "restaurant", "pizza",
                      "burger", "chocolate", "drink", "juice", "cake", "spice",
                      "recipe", "taste", "flavor"], "Warm", "Energetic"),
    ("tech", ["app", "software", "phone", "laptop", "gadget", "ai ", "tech",
              "device", "camera", "drone", "smart", "wireless", "charger"], "Cool", "Minimal"),
    ("fitness", ["fitness", "gym", "workout", "protein", "sport", "yoga", "run",
                 "muscle", "training", "health"], "Vibrant", "Energetic"),
    ("luxury", ["luxury", "gold", "premium", "diamond", "watch", "jewel", "leather",
                "exclusive", "handmade"], "Cinematic", "Luxury"),
    ("fashion", ["fashion", "dress", "shoe", "clothes", "style", "wear", "bag",
                 "outfit"], "Vintage", "Elegant"),
    ("home", ["home", "kitchen", "furniture", "decor", "garden", "clean", "sofa",
              "lamp", "candle"], "Warm", "Minimal"),
    ("kids & baby", ["baby", "kid", "toy", "child", "diaper", "nursery"], "Vibrant", "Funny"),
    ("travel", ["travel", "hotel", "flight", "beach", "trip", "vacation", "tour"],
     "Cinematic", "Cinematic"),
    ("pet", ["dog", "cat", "pet", "puppy", "kitten", "vet"], "Warm", "Funny"),
]


def _get(url, params, timeout=10):
    resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp


def search_suggestions(term: str, limit=8):
    out, errors = [], []
    try:
        data = _get(GOOGLE_SUGGEST, {"client": "firefox", "q": term}, timeout=8)
        out.extend(data.json()[1][:limit])
    except Exception as exc:
        errors.append(f"google: {exc}")
    if len(out) < 3:
        try:
            data = _get(DDG_AC, {"q": term, "type": "list"}, timeout=8)
            out.extend(data.json()[1][:limit])
        except Exception as exc:
            errors.append(f"ddg: {exc}")
    seen, uniq = set(), []
    for s in out:
        s = " ".join(str(s).split())
        k = s.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    return uniq[:limit], errors


def wiki_trend(term: str):
    try:
        data = _get(WIKI_SEARCH, {"action": "query", "list": "search",
                                  "srsearch": term, "format": "json", "srlimit": 1},
                    timeout=8).json()
        hits = data.get("query", {}).get("search", [])
        if not hits:
            return {"status": "no_data"}
        title = hits[0]["title"]
        end = time.gmtime(time.time())
        start_28 = time.gmtime(time.time() - 28 * 86400)
        mid = time.gmtime(time.time() - 14 * 86400)
        fmt = lambda t: time.strftime("%Y%m%d", t)
        url = WIKI_PAGEVIEWS.format(title=title.replace(" ", "_"),
                                    start=fmt(start_28), end=fmt(end))
        pv = requests.get(url, headers=HEADERS, timeout=10).json()
        items = pv.get("items", [])
        if len(items) < 8:
            return {"status": "no_data"}
        first = sum(i["views"] for i in items[:14]) or 1
        second = sum(i["views"] for i in items[14:]) or 1
        pct = int(round((second - first) / first * 100))
        if pct >= 10:
            status = "rising"
        elif pct <= -10:
            status = "falling"
        else:
            status = "stable"
        return {"status": status, "pct": pct, "article": title}
    except Exception:
        return {"status": "error"}


def score_tagline(tagline: str, product: str, suggestions) -> dict:
    tag = (tagline or "").strip()
    prod = (product or "").strip()
    breakdown = []
    if not tag:
        return {"score": 0, "breakdown": [("empty", 0, 0)], "issues": ["no tagline entered"]}
    n = len(tag)
    if 15 <= n <= 70:
        breakdown.append(("length", 20, 20 if n <= 55 else 16))
    elif 10 <= n <= 90:
        breakdown.append(("length", 20, 10))
    else:
        breakdown.append(("length", 20, 4))
    words = set(tag.lower().replace("-", " ").split())
    core = prod.lower().split()[:2] if prod else []
    mentions = any(c and c in tag.lower() for c in core)
    breakdown.append(("product mention", 20, 18 if mentions else (8 if core else 12)))
    power = any(w.strip(".,!?") in POWER_WORDS for w in words)
    breakdown.append(("impact words", 20, 18 if power else 6))
    concrete = any(ch.isdigit() for ch in tag) or len(words) >= 4
    breakdown.append(("specificity", 15, 13 if concrete else 5))
    rhythm = 2 <= len(tag.split()) <= 10 and not tag.endswith("..")
    breakdown.append(("rhythm", 15, 13 if rhythm else 6))
    sugg_words = {w for s in suggestions for w in s.lower().split()}
    overlap = len(words & sugg_words)
    if suggestions:
        breakdown.append(("search demand", 10, min(10, overlap * 4 + 3)))
    else:
        breakdown.append(("search demand", 10, 0))
    score = sum(v for _, _, v in breakdown)
    issues = []
    if not mentions and core:
        issues.append("does not mention the product")
    if not power:
        issues.append("no power/impact word")
    if n > 70:
        issues.append("too long for an end card")
    if n and n < 15:
        issues.append("very short - could carry more meaning")
    if not suggestions:
        issues.append("online search data unavailable")
    return {"score": score, "breakdown": breakdown, "issues": issues}


def detect_category(text: str):
    low = f" {text.lower()} "
    for name, keys, mood, style in CATEGORY_RULES:
        if any(k in low for k in keys):
            return name, mood, style
    return "general", "Auto (recommended)", None


def recommend(product: str, description: str, tagline: str):
    text = f"{product} {description} {tagline}"
    category, mood, style = detect_category(text)
    suggestions, errors = search_suggestions(product or tagline or "product")
    trend = wiki_trend(product or tagline or "")
    scored = score_tagline(tagline, product, suggestions)
    return {
        "category": category,
        "mood": mood,
        "style": style,
        "suggestions": suggestions,
        "trend": trend,
        "score": scored,
        "online": not errors or bool(suggestions),
        "errors": errors,
    }


def suggest_taglines(product: str, description: str, tagline: str, category: str, mood: str):
    from . import script_gen
    prompt = (
        f"Write 2 punchy advertising taglines (max 8 words each) for this product. "
        f"Product: {product}\nDescription: {description}\nCurrent tagline: {tagline or 'none'}\n"
        f"Category: {category}. Tone should fit a {mood} mood.\n"
        f'Reply ONLY with JSON: {{"a": "...", "b": "..."}} - plain text, no quotes inside, no emojis.'
    )
    try:
        content = script_gen._post_classic([{"role": "user", "content": prompt}], timeout=60)
        data = script_gen._extract_json(content)
        a = script_gen._clean(str(data.get("a", "")).strip().strip('"'))
        b = script_gen._clean(str(data.get("b", "")).strip().strip('"'))
        return [s for s in (a, b) if s][:2]
    except Exception:
        base = product or "it"
        fallback = [
            f"Discover {base} today",
            f"{base} - feel the difference",
        ]
        return fallback[:1] if tagline and tagline.lower() == fallback[0].lower() else fallback


def analyze(product: str, description: str, tagline: str) -> dict:
    """One comprehensive analysis: product understanding, category, mood/style,
    strict tagline score and 2 alternative taglines. Falls back to the
    heuristics when the LLM call fails."""
    from . import moods, script_gen
    from .script_gen import STYLES

    text = f"{product} {description} {tagline}"
    category0, mood0, style0 = detect_category(text)
    suggestions, errors = search_suggestions(product or tagline or "product")
    trend = wiki_trend(product or tagline or "")
    scored = score_tagline(tagline, product, suggestions)
    rec = {
        "category": category0,
        "mood": mood0,
        "style": style0,
        "suggestions": suggestions,
        "trend": trend,
        "score": scored,
        "online": not errors or bool(suggestions),
        "errors": errors,
        "understanding": "",
        "tagline_suggestions": [],
    }
    mood_names = [m for m in moods.MOOD_NAMES]
    prompt = (
        "You are an advertising strategist. First truly understand the product from the "
        "name and full description below, then evaluate its tagline.\n"
        f"Product name: {product or 'unknown'}\n"
        f"Full description: {description or 'none'}\n"
        f"Current tagline: {tagline or 'none'}\n"
        f"Real search queries people use for this product: {', '.join(suggestions[:6]) or 'unknown'}\n"
        f"Wikipedia interest trend (last 28 days): {trend.get('status', 'unknown')}\n\n"
        f"Allowed moods: {', '.join(mood_names)}.\n"
        f"Allowed styles: {', '.join(STYLES)}.\n\n"
        "Reply ONLY with compact JSON (no markdown, no extra text):\n"
        '{"understanding": "2-3 sentences: what the product is, who it is for, its main benefit",'
        ' "category": "short category label",'
        ' "mood": "one allowed mood", "style": "one allowed style",'
        ' "score": 0-100, how strong the tagline is for this product (be strict: 50 = average),'
        ' "issues": ["up to 3 concrete problems with the tagline"],'
        ' "taglines": ["alternative 1, max 8 words", "alternative 2, max 8 words"]}'
    )
    try:
        content = script_gen._post_classic([{"role": "user", "content": prompt}], timeout=75)
        data = script_gen._extract_json(content)
        understanding = script_gen._clean(str(data.get("understanding", "") or ""))
        if understanding:
            rec["understanding"] = understanding[:400]
        cat = str(data.get("category", "") or "").strip().lower()[:40]
        if cat:
            rec["category"] = cat
        mood = str(data.get("mood", "") or "").strip()
        if mood in mood_names:
            rec["mood"] = mood
        style = str(data.get("style", "") or "").strip()
        if style in STYLES:
            rec["style"] = style
        try:
            llm_score = int(round(float(data.get("score"))))
        except (TypeError, ValueError):
            llm_score = 0
        if 0 < llm_score <= 100:
            issues = [script_gen._clean(str(i)) for i in (data.get("issues") or [])]
            issues = [i for i in issues if i][:3]
            rec["score"] = {
                "score": llm_score,
                "breakdown": scored["breakdown"],
                "issues": issues or scored["issues"],
            }
        tags = []
        for t in (data.get("taglines") or [])[:2]:
            t = script_gen._clean(str(t).strip().strip('"'))
            if t and len(t) <= 80:
                tags.append(t)
        rec["tagline_suggestions"] = tags
    except Exception:
        pass
    if not rec["tagline_suggestions"]:
        rec["tagline_suggestions"] = suggest_taglines(
            product, description, tagline, rec["category"], rec["mood"])
    return rec
