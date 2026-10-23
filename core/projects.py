import json
import shutil
import time
from pathlib import Path

STAGES = ("script", "visuals", "voice", "compose")


def slugify(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in (name or "ad").lower())[:30].strip("_") or "ad"


def _new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def save(pdir: Path, data: dict) -> None:
    data["updated"] = time.time()
    (pdir / "project.json").write_text(json.dumps(data, indent=2, ensure_ascii=False),
                                      encoding="utf-8")


def load(pdir: Path) -> dict:
    data = json.loads((pdir / "project.json").read_text(encoding="utf-8"))
    data["dir"] = str(pdir)
    return data


def create(output_dir: Path, params: dict, reference_image: str = "") -> dict:
    pid = _new_id()
    slug = slugify(params.get("product", "ad"))
    pdir = output_dir / f"{pid}_{slug}"
    n = 1
    while pdir.exists():
        pdir = output_dir / f"{pid}_{slug}_{n}"
        n += 1
    (pdir / "work").mkdir(parents=True)
    params = dict(params)
    if reference_image and Path(reference_image).exists():
        ext = Path(reference_image).suffix.lower() or ".jpg"
        dest = pdir / f"reference{ext}"
        try:
            shutil.copy2(reference_image, dest)
            params["reference_image"] = str(dest)
        except OSError:
            params["reference_image"] = reference_image
    data = {
        "id": pid,
        "created": time.time(),
        "updated": time.time(),
        "status": "running",
        "stages": {s: False for s in STAGES},
        "params": params,
        "video": "",
        "error": "",
    }
    save(pdir, data)
    data["dir"] = str(pdir)
    return data


def list_projects(output_dir) -> list:
    out = []
    root = Path(output_dir)
    if not root.is_dir():
        return out
    for d in root.iterdir():
        pf = d / "project.json"
        if not d.is_dir() or not pf.is_file():
            continue
        try:
            data = json.loads(pf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        data["dir"] = str(d)
        out.append(data)
    out.sort(key=lambda p: p.get("updated", 0), reverse=True)
    return out


def next_stage(data: dict):
    for s in STAGES:
        if not data.get("stages", {}).get(s):
            return s
    return None


def mark_stage(pdir: Path, data: dict, stage: str) -> None:
    data.setdefault("stages", {})[stage] = True
    save(pdir, data)


def set_status(pdir: Path, data: dict, status: str, video: str = "", error: str = "") -> None:
    data["status"] = status
    if video:
        data["video"] = video
    if error:
        data["error"] = error
    save(pdir, data)


def delete(pdir) -> None:
    shutil.rmtree(pdir, ignore_errors=True)


def is_usable_file(path, min_bytes: int) -> bool:
    p = Path(path)
    return p.is_file() and p.stat().st_size >= min_bytes
