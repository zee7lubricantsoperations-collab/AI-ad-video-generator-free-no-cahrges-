# AI Ad Video Maker (Windows)

Desktop app that creates 7–10 second advertisement videos with AI-generated
script, visuals, voiceover (21 languages), captions, and background music.

## Quick start

**Run the app:** double-click `dist\AdVideoMaker.exe` (no install needed)

**Or run from source:**

```powershell
pip install -r requirements.txt
python app.py
```

**Rebuild the .exe:**

```powershell
.\build.ps1
```

## Setup

1. Open the app → **Settings** tab
2. Click **Get free key (browser)** → sign in at enter.pollinations.ai
   (GitHub login) → copy your `sk_...` key
3. Paste the key → **Save key** → **Test key** (shows pollen balance)
4. Choose output folder → **Save settings**

**Without a key** the app still works in fallback mode: AI-generated image
with cinematic pan/zoom animation + voiceover. With a free key you get true
AI video models (Seedance, Wan, Veo) paid from free pollen credits
(~0.04–0.8 pollen per ad depending on model).

## Usage

**Create Ad** tab:

| Field | Notes |
|---|---|
| Product name / Description | Required — AI writes the ad from these |
| Tagline | Optional — shown at the end of the video |
| Language | 21 languages (English, Spanish, Arabic, Hindi, ...) |
| Voice | Male / Female neural voice |
| Style | Energetic, Elegant, Funny, Luxury, Minimal, Cinematic |
| Aspect ratio | 9:16 (TikTok/Reels) or 16:9 (YouTube/Web) |
| Duration | 7–10 seconds |
| Video source | Auto (AI video with key) or Fallback (animated image) |
| Captions / Music | Burn-in subtitles, soft background pad |
| Reference image | Optional product photo — Rotate / Crop before generating |

Click **Generate Ad** → progress appears in the log → **Preview last video**
or **Open output folder**. MP4 files (H.264 + AAC) are saved to the output
folder (default `Videos\AIAds`).

**Recent** tab lists every generation as a project. Interrupted or failed
projects resume from the last completed stage (script → visuals → voice →
compose); each scene shot and AI image is cached inside the project folder.
"Create similar" loads a project's settings back into the Create Ad form.

## How it works

1. **Script** — LLM writes a voiceover line in your language + an English
   visual prompt (Pollinations keyed API, keyless classic fallback)
2. **Visuals** — key: async AI video job (Seedance 2.0 Mini default,
   Wan 2.6, Veo 3.1 Fast); no key: AI storyboard — up to 3 AI scenes
   (hero, lifestyle, macro detail), each animated as 2 eased camera shots
   with varied transitions (slide/wipe/dissolve/circle) + tagline end card
3. **Voiceover** — Microsoft Edge neural TTS (edge-tts), auto speed-fitted
   to the target duration
4. **Compose** — bundled ffmpeg loops/trims visuals, mixes voice + music,
   burns captions (libass, RTL-safe), scales to 1080×1920 or 1920×1080

**Tagline Insights** runs one product-understanding analysis (category,
mood/style, strict 0–100 score, alternative taglines) plus live search
suggestions and Wikipedia trend.

## Project layout

```
app.py              GUI (CustomTkinter: Create / Recent / Settings)
core/config.py      settings.json persistence
core/languages.py   21 languages -> edge-tts voices
core/script_gen.py  LLM ad copy
core/video_gen.py   AI video + storyboard fallback (multi-shot)
core/tts.py         voiceover + duration fitting
core/compose.py     ffmpeg assembly, captions, music
core/analytics.py   tagline research (search + LLM analysis)
core/projects.py    per-project folders + resume stage tracking
core/pipeline.py    end-to-end orchestration (staged, resumable)
tests/e2e_test.py   headless pipeline test
```

## Tests

```powershell
python tests\e2e_test.py --aspect 9:16   # full keyless pipeline
python tests\e2e_test.py --aspect 16:9
```

## Notes

- Needs internet (AI services + TTS); ffmpeg is bundled (no install)
- API key is stored locally in `settings.json` next to the .exe
- Pollinations free tier is rate/credit limited; the keyless fallback
  keeps the app usable when credits run out
