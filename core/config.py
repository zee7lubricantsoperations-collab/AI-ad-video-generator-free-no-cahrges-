import json
import sys
from pathlib import Path

APP_NAME = "AI Ad Video Maker"

DEFAULTS = {
    "api_key": "",
    "output_dir": "",
    "default_language": "English",
    "default_model": "auto",
    "aspect": "9:16",
    "duration": 8,
    "captions": True,
    "music": True,
}


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


CONFIG_PATH = app_dir() / "settings.json"


def default_output_dir() -> Path:
    portable = app_dir() / "output"
    if getattr(sys, "frozen", False) and portable.is_dir():
        return portable
    return Path.home() / "Videos" / "AIAds"


def load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    if not cfg.get("output_dir"):
        cfg["output_dir"] = str(default_output_dir())
    return cfg


def save(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
