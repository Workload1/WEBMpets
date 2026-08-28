#!/usr/bin/env python3
"""Build and install a Codex V1 pet from a local action bitmap directory.

The input directory name becomes the pet id (for example ``myrtle``). Files
are assigned by action names in their stems: ``idle.png``, ``idle-001.png``,
``running-right-03.webp``. A single image is accepted as a one-frame action;
the action is repeated to satisfy the V1 row contract. WEBM files are also
accepted as a compatibility fallback and are processed through process_webm.py.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

import numpy as np
from PIL import Image


CELL = (192, 208)
ROWS = [
    ("idle", 6),
    ("running-right", 8),
    ("running-left", 8),
    ("waving", 4),
    ("jumping", 5),
    ("failed", 8),
    ("waiting", 6),
    ("running", 6),
    ("review", 6),
]
IMAGE_EXTS = {".png", ".webp", ".jpg", ".jpeg", ".bmp"}
WEBM_EXTS = {".webm", ".mp4", ".mkv"}
ACTION_ALIASES = {
    "running_right": "running-right",
    "runningright": "running-right",
    "running_left": "running-left",
    "runningleft": "running-left",
}


def die(message: str) -> NoReturn:
    raise SystemExit(f"build_bitmap_pet: {message}")


def action_from_name(path: Path) -> str | None:
    stem = path.stem.lower()
    # Remove trailing frame/index tokens while retaining hyphenated actions.
    stem = re.sub(r"(?:[-_.](?:frame|f)?\d+)$", "", stem)
    stem = ACTION_ALIASES.get(stem, stem)
    known = {name for name, _count in ROWS}
    if stem in known:
        return stem
    for name in sorted(known, key=len, reverse=True):
        if stem.startswith(name + "-") or stem.startswith(name + "_"):
            return name
    return None


def fit_bitmap(path: Path, key: str | None) -> Image.Image:
    with Image.open(path) as opened:
        image = opened.convert("RGBA")
    if key and image.getextrema()[3][1] == 255 and image.getextrema()[3][0] == 255:
        rgb = np.asarray(image)[..., :3].astype(np.int16)
        target = 0 if key == "near-black" else 255
        distance = np.max(np.abs(rgb - target), axis=2)
        alpha = np.where(distance <= 12, 0, 255).astype(np.uint8)
        rgba = np.dstack([np.asarray(image)[..., :3], alpha])
        rgba[alpha == 0, :3] = 0
        image = Image.fromarray(rgba, "RGBA")
    bbox = image.getbbox()
    if bbox:
        image = image.crop(bbox)
    if image.width == 0 or image.height == 0:
        die(f"empty bitmap: {path}")
    scale = min((CELL[0] - 8) / image.width, (CELL[1] - 8) / image.height)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    image = image.resize(size, Image.Resampling.LANCZOS)
    cell = Image.new("RGBA", CELL, (0, 0, 0, 0))
    cell.alpha_composite(image, ((CELL[0] - image.width) // 2, (CELL[1] - image.height) // 2))
    return cell


def normalize_source(path: Path, key: str | None) -> Image.Image:
    """Load an image and normalize it to one transparent 192x208 cell."""
    with Image.open(path) as opened:
        image = opened.convert("RGBA")
    # An already-sized image is safe to reuse only when it already contains
    # transparency. Opaque 192x208 exports still need chroma-key cleanup.
    alpha_extrema = image.getchannel("A").getextrema()
    if image.size == CELL and image.getbbox() is not None and alpha_extrema[0] < 255:
        return image
    return fit_bitmap(path, key)


def select_frames(files: list[Path], count: int) -> list[Path]:
    if not files:
        return []
    if len(files) >= count:
        indexes = [round(i * (len(files) - 1) / max(1, count - 1)) for i in range(count)]
        return [files[i] for i in indexes]
    # A single still (or a short sequence) is valid as a fallback: repeat its
    # last available frame instead of inventing new pixels.
    return [files[min(i, len(files) - 1)] for i in range(count)]


def load_action_files(input_dir: Path, action: str) -> list[Path]:
    files = []
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTS | WEBM_EXTS:
            continue
        if action_from_name(path) == action:
            files.append(path)
    return files


def prepare_output_dir(output_dir: Path, input_dir: Path) -> None:
    """Safely recreate a generated intermediate directory.

    Never remove an arbitrary existing directory: require the build marker to
    prove that it belongs to this script and this input directory.
    """
    marker = output_dir / "input.json"
    if output_dir.exists():
        if not marker.is_file():
            die(f"output directory exists without build marker; choose another --output: {output_dir}")
        try:
            previous = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            die(f"cannot read output build marker {marker}: {exc}")
        if Path(previous.get("input_dir", "")).resolve() != input_dir.resolve():
            die(f"output directory belongs to a different input; choose another --output: {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def process_webm(python: Path, script: Path, source: Path, output: Path, key: str, fps: float) -> list[Path]:
    if source.stat().st_size < 1024:
        die(f"video is too small to contain a usable animation: {source}")
    output.mkdir(parents=True, exist_ok=True)
    if getattr(sys, "frozen", False) and python.resolve() == Path(sys.executable).resolve():
        # A PyInstaller worker can dispatch the WebM helper from inside the
        # same executable; no source-file path is needed in the frozen build.
        cmd = [str(python), "--input", str(source), "--output", str(output), "--key", key, "--cell", "192x208", "--fps", str(fps)]
    else:
        cmd = [str(python), str(script), "--input", str(source), "--output", str(output), "--key", key, "--cell", "192x208", "--fps", str(fps)]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        die(result.stderr.strip() or result.stdout.strip() or f"process_webm failed for {source}")
    return sorted((output / "frames").glob("frame-*.png"))


def write_contact_sheet(action_dirs: dict[str, Path], output: Path) -> None:
    thumbs = []
    for action, _count in ROWS:
        for path in sorted((action_dirs[action]).glob("frame-*.png")):
            with Image.open(path) as image:
                thumb = image.convert("RGBA")
                thumb.thumbnail((96, 104))
                thumbs.append((action, thumb.copy()))
    if not thumbs:
        return
    cols = min(10, len(thumbs))
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGBA", (cols * 110, rows * 125), (235, 235, 235, 255))
    from PIL import ImageDraw
    draw = ImageDraw.Draw(sheet)
    for i, (action, image) in enumerate(thumbs):
        x, y = (i % cols) * 110, (i // cols) * 125
        sheet.alpha_composite(image, (x + (110 - image.width) // 2, y + 2))
        draw.text((x + 2, y + 106), f"{action} {i}", fill=(0, 0, 0, 255))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.convert("RGB").save(output)


def compose_and_install(input_dir: Path, output_dir: Path, pet_dir: Path, pet_name: str, mapping: dict, overwrite: bool) -> dict:
    action_dirs = {action: output_dir / "actions" / action for action, _count in ROWS}
    for action, count in ROWS:
        files = sorted(action_dirs[action].glob("frame-*.png"))
        if len(files) < count:
            return {"status": "blocked", "error": f"{action} has {len(files)} frames; requires {count}"}
    if pet_dir.exists() and any(pet_dir.iterdir()) and not overwrite:
        return {"status": "blocked", "error": f"pet directory is not empty; pass --overwrite: {pet_dir}"}
    pet_dir.mkdir(parents=True, exist_ok=True)
    atlas = Image.new("RGBA", (1536, 1872), (0, 0, 0, 0))
    for row, (action, count) in enumerate(ROWS):
        for col, path in enumerate(sorted(action_dirs[action].glob("frame-*.png"))[:count]):
            with Image.open(path) as image:
                atlas.alpha_composite(image.convert("RGBA"), (col * CELL[0], row * CELL[1]))
    sheet = pet_dir / "spritesheet.webp"
    atlas.save(sheet, format="WEBP", lossless=True, quality=100, method=6, exact=True)
    manifest = {
        "id": pet_name,
        "displayName": mapping.get("display_name") or pet_name,
        "description": mapping.get("description") or f"由本地位图目录 {input_dir.name} 生成的 V1 桌宠。",
        "spriteVersionNumber": 1,
        "spritesheetPath": "spritesheet.webp",
    }
    if isinstance(mapping.get("animationMapping"), dict):
        manifest["animationMapping"] = mapping["animationMapping"]
    (pet_dir / "pet.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": "installed", "pet_name": pet_name, "pet_dir": str(pet_dir), "spritesheet": str(sheet), "pet_json": str(pet_dir / "pet.json"), "dimensions": [1536, 1872]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bitmap-dir", "--input-dir", dest="input_dir", required=True, help="directory containing action bitmaps or compatible WEBM files")
    parser.add_argument("--pet-dir", "--install-dir", dest="pet_dir", help="custom Codex pet directory; defaults to ~/.codex/pets/<input-folder-name>")
    parser.add_argument("--output", help="intermediate run directory; defaults to a sibling .pet-build directory")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--process-script", help="process_webm.py path")
    parser.add_argument("--fps", type=float, default=30)
    parser.add_argument("--key", choices=["near-black", "near-white"], default="near-black")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--mapping-json", help="optional JSON containing display_name, description, and animationMapping")
    args = parser.parse_args()

    input_dir = Path(args.input_dir).expanduser().resolve()
    if not input_dir.is_dir():
        die(f"input directory does not exist: {input_dir}")
    pet_name = input_dir.name
    output_dir = Path(args.output).expanduser().resolve() if args.output else input_dir.parent / f".{pet_name}-pet-build"
    default_pet_root = Path.home() / ".codex" / "pets"
    pet_dir = Path(args.pet_dir).expanduser() if args.pet_dir else default_pet_root / pet_name
    pet_dir = pet_dir.resolve()
    mapping = json.loads(Path(args.mapping_json).read_text(encoding="utf-8")) if args.mapping_json else {}
    process_script = Path(args.process_script).expanduser().resolve() if args.process_script else Path(__file__).with_name("process_webm.py")
    python = Path(args.python).expanduser().resolve()

    if output_dir == input_dir or output_dir == pet_dir:
        die("--output must be different from --bitmap-dir and --pet-dir")
    prepare_output_dir(output_dir, input_dir)
    (output_dir / "input.json").write_text(json.dumps({"input_dir": str(input_dir), "pet_name": pet_name, "pet_dir": str(pet_dir)}, ensure_ascii=False, indent=2), encoding="utf-8")
    action_dirs = {action: output_dir / "actions" / action for action, _count in ROWS}
    statuses = {}
    for action, count in ROWS:
        sources = load_action_files(input_dir, action)
        if not sources:
            statuses[action] = {"status": "missing"}
            continue
        if action == "running-left" and any(p.suffix.lower() in IMAGE_EXTS | WEBM_EXTS for p in sources):
            # An explicit running-left source wins over the mirror fallback.
            pass
        source_images = []
        for index, source in enumerate(sources):
            if source.suffix.lower() in WEBM_EXTS:
                source_images.extend(process_webm(python, process_script, source, output_dir / "decoded" / action / str(index), args.key, args.fps))
            else:
                source_images.append(source)
        selected = select_frames(sorted(source_images), count)
        action_dirs[action].mkdir(parents=True, exist_ok=True)
        for i, source in enumerate(selected):
            if source.suffix.lower() in IMAGE_EXTS:
                image = normalize_source(source, args.key)
            else:
                with Image.open(source) as opened:
                    image = opened.convert("RGBA")
                if image.size != CELL:
                    image = fit_bitmap(source, args.key)
            image.save(action_dirs[action] / f"frame-{i:04d}.png")
        statuses[action] = {"status": "ready", "sources": [str(p) for p in sources], "frames": len(selected)}

    # Derive running-left only after running-right has been prepared.
    if statuses.get("running-left", {}).get("status") != "ready" and statuses.get("running-right", {}).get("status") == "ready":
        action_dirs["running-left"].mkdir(parents=True, exist_ok=True)
        for path in sorted(action_dirs["running-right"].glob("frame-*.png")):
            with Image.open(path) as image:
                image.transpose(Image.Transpose.FLIP_LEFT_RIGHT).save(action_dirs["running-left"] / path.name)
        statuses["running-left"] = {"status": "mirrored", "source_action": "running-right", "frames": len(list(action_dirs["running-left"].glob("frame-*.png")))}
    write_contact_sheet(action_dirs, output_dir / "contact-sheet.png")
    install = compose_and_install(input_dir, output_dir, pet_dir, pet_name, mapping, args.overwrite)
    report = {"input_dir": str(input_dir), "pet_name": pet_name, "pet_dir": str(pet_dir), "actions": statuses, "install": install, "version": 1}
    (output_dir / "build-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if getattr(sys, "frozen", False) and "--input" in sys.argv:
        # Hidden-import this module when building the worker executable.
        from process_webm import main as process_webm_main
        process_webm_main()
    else:
        main()
