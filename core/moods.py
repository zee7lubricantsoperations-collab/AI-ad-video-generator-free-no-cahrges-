MOODS = {
    "Auto (recommended)": {
        "grade": "",
        "prompt": "",
        "bright": True,
        "accent": "#7C5CFF",
        "top": "#0B0E14",
        "bottom": "#1A1F30",
    },
    "Vibrant": {
        "grade": "eq=saturation=1.35:contrast=1.06:brightness=0.02",
        "prompt": "vivid saturated colors, punchy modern color grading",
        "bright": True,
        "accent": "#FF5C7A",
        "top": "#141026",
        "bottom": "#2A1440",
    },
    "Cinematic": {
        "grade": "eq=contrast=1.12:saturation=0.95,vignette=PI/5",
        "prompt": "cinematic teal and orange color grading, filmic look",
        "bright": True,
        "accent": "#FF9F4A",
        "top": "#0A1418",
        "bottom": "#152229",
    },
    "Warm": {
        "grade": "eq=saturation=1.12:brightness=0.02,colorbalance=rs=.06:gs=.02:bs=-.06",
        "prompt": "warm golden sunlight tones, cozy inviting atmosphere",
        "bright": True,
        "accent": "#FFB347",
        "top": "#181008",
        "bottom": "#2E1F10",
    },
    "Cool": {
        "grade": "eq=saturation=1.05,colorbalance=rs=-.06:bs=.07",
        "prompt": "cool blue tones, crisp clean futuristic atmosphere",
        "bright": False,
        "accent": "#5CB8FF",
        "top": "#081018",
        "bottom": "#101E2E",
    },
    "Noir": {
        "grade": "hue=s=0,eq=contrast=1.28:brightness=-0.02",
        "prompt": "black and white film noir, high contrast monochrome, dramatic shadows",
        "bright": False,
        "accent": "#E8E8E8",
        "top": "#0A0A0A",
        "bottom": "#1C1C1C",
    },
    "Vintage": {
        "grade": "eq=saturation=0.72:contrast=0.96:gamma=1.06,colorbalance=rs=.05:bs=-.05,vignette=PI/6",
        "prompt": "vintage faded film look, retro warm tones, soft grain",
        "bright": True,
        "accent": "#D8B56A",
        "top": "#141008",
        "bottom": "#26200F",
    },
}

MOOD_NAMES = list(MOODS.keys())


def mood_key(name: str) -> str:
    return name if name in MOODS else "Auto (recommended)"


def grade_for(name: str) -> str:
    return MOODS[mood_key(name)]["grade"]


def prompt_for(name: str) -> str:
    return MOODS[mood_key(name)]["prompt"]


def is_bright(name: str) -> bool:
    return MOODS[mood_key(name)]["bright"]


def endcard_colors(name: str):
    m = MOODS[mood_key(name)]
    return m["top"], m["bottom"], m["accent"]
