import importlib
import subprocess
import sys
from pathlib import Path

import requests

from .ffmpeg_util import CREATE_NO_WINDOW, ffmpeg_exe

PACKAGES = [
    ("customtkinter", "customtkinter"),
    ("edge_tts", "edge-tts"),
    ("requests", "requests"),
    ("imageio_ffmpeg", "imageio-ffmpeg"),
    ("PIL", "pillow"),
]

FROZEN = getattr(sys, "frozen", False)


def _check_packages():
    missing = []
    for module, package in PACKAGES:
        try:
            importlib.import_module(module)
            yield {"name": f"package: {package}", "ok": True, "detail": "installed"}
        except ImportError:
            missing.append(package)
            yield {"name": f"package: {package}", "ok": False, "detail": "NOT installed"}
    if missing and not FROZEN:
        yield {"name": "auto-install", "ok": None, "detail": f"installing: {', '.join(missing)}"}
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *missing],
                capture_output=True,
                text=True,
                timeout=600,
                creationflags=CREATE_NO_WINDOW,
            )
            for module, package in PACKAGES:
                if package in missing:
                    try:
                        importlib.import_module(module)
                        yield {"name": f"package: {package}", "ok": True, "detail": "installed now"}
                    except ImportError:
                        yield {"name": f"package: {package}", "ok": False, "detail": "install FAILED"}
        except Exception as exc:
            yield {"name": "auto-install", "ok": False, "detail": f"install error: {exc}"}


def _check_ffmpeg():
    try:
        proc = subprocess.run(
            [ffmpeg_exe(), "-version"],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=CREATE_NO_WINDOW,
        )
        if proc.returncode == 0:
            first = (proc.stdout or "").splitlines()[0]
            yield {"name": "ffmpeg (bundled)", "ok": True, "detail": first}
        else:
            yield {"name": "ffmpeg (bundled)", "ok": False, "detail": "ffmpeg returned an error"}
    except Exception as exc:
        yield {"name": "ffmpeg (bundled)", "ok": False, "detail": str(exc)}


def _check_font():
    font = Path("C:/Windows/Fonts/arial.ttf")
    if font.exists():
        yield {"name": "captions font (Arial)", "ok": True, "detail": "arial.ttf found"}
    else:
        alt = Path("C:/Windows/Fonts/segoeui.ttf")
        if alt.exists():
            yield {"name": "captions font (Segoe UI)", "ok": True, "detail": "arial missing, segoeui fallback OK"}
        else:
            yield {"name": "captions font", "ok": False, "detail": "no system font for captions"}


def _check_network():
    try:
        requests.head("https://gen.pollinations.ai", timeout=8, allow_redirects=True)
        yield {"name": "internet / AI services", "ok": True, "detail": "reachable"}
    except Exception:
        try:
            requests.head("https://www.microsoft.com", timeout=8)
            yield {"name": "internet / AI services", "ok": False, "detail": "online but AI services unreachable"}
        except Exception:
            yield {"name": "internet / AI services", "ok": False, "detail": "OFFLINE — generation will fail"}


def run_checks():
    results = []
    results.extend(_check_packages())
    results.extend(_check_ffmpeg())
    results.extend(_check_font())
    results.extend(_check_network())
    return results


def format_report(results) -> str:
    lines = []
    for r in results:
        mark = "OK" if r["ok"] is True else (".." if r["ok"] is None else "!!")
        lines.append(f"[{mark}] {r['name']}: {r['detail']}")
    failed = [r for r in results if r["ok"] is False]
    if failed:
        lines.append(f"-- {len(failed)} problem(s) found --")
    else:
        lines.append("-- all checks passed --")
    return "\n".join(lines)


def all_ok(results) -> bool:
    return all(r["ok"] is not False for r in results)
