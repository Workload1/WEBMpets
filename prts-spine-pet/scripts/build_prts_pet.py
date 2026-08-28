#!/usr/bin/env python3
"""Drive the deterministic part of the PRTS-to-pet pipeline from a JSON request.

The language model (or a user) supplies a request JSON.  This script validates
the request, processes each available WebM through ``process_webm.py``,
optionally mirrors ``running-left``, resamples each action to a fixed frame
count, and writes machine-readable reports.  It deliberately does not guess
missing PRTS assets or synthesize V2 look-direction rows.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

from PIL import Image, ImageDraw


CELL = (192, 208)
STANDARD_COUNTS = {
    "idle": 6,
    "running-right": 8,
    "running-left": 8,
    "waving": 4,
    "jumping": 5,
    "failed": 8,
    "waiting": 6,
    "running": 6,
    "review": 6,
}


def die(message: str) -> NoReturn:
    raise SystemExit(f"build_prts_pet: {message}")


def load_request(path: Path) -> dict:
    try:
        request = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        die(f"cannot read request JSON {path}: {exc}")
    if not isinstance(request, dict):
        die("request root must be a JSON object")
    for key in ("operator", "costume"):
        if not isinstance(request.get(key), str) or not request[key].strip():
            die(f"request.{key} must be a non-empty string")
    if not isinstance(request.get("actions"), dict) or not request["actions"]:
        die("request.actions must be a non-empty object")
    if "sources" in request and not isinstance(request["sources"], dict):
        die("request.sources must be an object when present")
    request.setdefault("version", 1)
    request.setdefault("fps", 30)
    request.setdefault("frames_per_action", 8)
    request.setdefault("key", "near-black")
    if request["version"] not in (1, 2):
        die("request.version must be 1 or 2")
    if request["key"] not in ("near-black", "near-white"):
        die("request.key must be near-black or near-white")
    if not isinstance(request["fps"], (int, float)) or request["fps"] <= 0:
        die("request.fps must be positive")
    if not isinstance(request["frames_per_action"], int) or request["frames_per_action"] <= 0:
        die("request.frames_per_action must be a positive integer")
    return request


def resolve_source(action: str, spec: object, sources: dict, base_dir: Path) -> tuple[Path | None, bool]:
    """Return (path, mirror) for one action."""
    if isinstance(spec, str) and spec.startswith("mirror(") and spec.endswith(")"):
        return None, True
    value = sources.get(action, spec)
    if not isinstance(value, str) or not value.strip():
        return None, False
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path, False


def process_webm(python: Path, script: Path, source: Path, action_dir: Path, key: str, fps: float) -> dict:
    if not source.exists():
        return {"status": "missing", "source": str(source)}
    if source.stat().st_size < 1024:
        return {"status": "invalid", "source": str(source), "error": "file is too small to contain a usable WebM"}
    action_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(python), str(script), "--input", str(source), "--output", str(action_dir),
        "--key", key, "--cell", f"{CELL[0]}x{CELL[1]}", "--fps", str(fps),
    ]
    # PRTS/FFmpeg helpers may emit the Windows code page even when the parent
    # process requests UTF-8; replacement keeps a diagnostic from breaking the
    # orchestration thread while the JSON report remains UTF-8.
    completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode:
        return {
            "status": "failed",
            "source": str(source),
            "error": completed.stderr.strip() or completed.stdout.strip(),
        }
    report_path = action_dir / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    report["status"] = "processed"
    return report


def frame_files(folder: Path) -> list[Path]:
    return sorted(folder.glob("frame-*.png"))


def select_frames(files: list[Path], count: int) -> list[Path]:
    if not files:
        return []
    if len(files) <= count:
        return files
    if count == 1:
        return [files[0]]
    indexes = [round(i * (len(files) - 1) / (count - 1)) for i in range(count)]
    return [files[i] for i in indexes]


def copy_selected(files: list[Path], destination: Path) -> list[str]:
    destination.mkdir(parents=True, exist_ok=True)
    selected = []
    for index, source in enumerate(files):
        target = destination / f"frame-{index:04d}.png"
        shutil.copy2(source, target)
        selected.append(str(target))
    return selected


def compose_v1(action_dirs: dict[str, Path], output: Path) -> Path:
    """Compose the nine standard rows into the Codex V1 atlas."""
    atlas = Image.new("RGBA", (8 * CELL[0], 9 * CELL[1]), (0, 0, 0, 0))
    for row, action in enumerate(STANDARD_COUNTS):
        files = frame_files(action_dirs[action])
        needed = STANDARD_COUNTS[action]
        if len(files) < needed:
            die(f"{action} needs {needed} frames for V1, found {len(files)}")
        for column, frame_path in enumerate(files[:needed]):
            with Image.open(frame_path) as opened:
                frame = opened.convert("RGBA")
                if frame.size != CELL:
                    die(f"{frame_path} is {frame.size}, expected {CELL}")
                atlas.alpha_composite(frame, (column * CELL[0], row * CELL[1]))
    output.parent.mkdir(parents=True, exist_ok=True)
    atlas.save(output, format="WEBP", lossless=True, quality=100, method=6, exact=True)
    return output


def install_v1(request: dict, run_dir: Path, install_dir: Path, action_dirs: dict[str, Path], overwrite: bool) -> dict:
    if request.get("version", 1) != 1:
        return {"status": "blocked", "error": "direct installation currently supports V1 only; set version to 1"}
    missing = [action for action in STANDARD_COUNTS if action not in action_dirs]
    if missing:
        return {"status": "blocked", "error": f"missing V1 actions: {', '.join(missing)}"}
    for action, needed in STANDARD_COUNTS.items():
        if len(frame_files(action_dirs[action])) < needed:
            return {"status": "blocked", "error": f"{action} has fewer than {needed} frames"}
    if install_dir.exists() and any(install_dir.iterdir()) and not overwrite:
        return {"status": "blocked", "error": f"install directory is not empty; pass --overwrite: {install_dir}"}
    install_dir.mkdir(parents=True, exist_ok=True)
    staged = compose_v1(action_dirs, run_dir / "final" / "spritesheet.webp")
    target_sheet = install_dir / "spritesheet.webp"
    shutil.copy2(staged, target_sheet)
    pet_id = request.get("pet_id") or install_dir.name
    pet = {
        "id": pet_id,
        "displayName": request.get("display_name") or request["operator"],
        "description": request.get("description") or f"基于 PRTS Wiki {request['operator']}「{request['costume']}」素材制作的 V1 桌宠。",
        "spriteVersionNumber": 1,
        "spritesheetPath": "spritesheet.webp",
    }
    if isinstance(request.get("mapping"), dict):
        pet["animationMapping"] = request["mapping"]
    (install_dir / "pet.json").write_text(json.dumps(pet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": "installed", "directory": str(install_dir), "spritesheet": str(target_sheet), "pet_json": str(install_dir / "pet.json"), "dimensions": [1536, 1872]}


def make_contact_sheet(action_dirs: list[tuple[str, Path]], output: Path) -> None:
    thumbs: list[tuple[str, Image.Image]] = []
    for action, folder in action_dirs:
        for path in frame_files(folder):
            with Image.open(path) as opened:
                image = opened.convert("RGBA")
                image.thumbnail((96, 104))
                thumbs.append((action, image.copy()))
    if not thumbs:
        return
    cols = min(10, len(thumbs))
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGBA", (cols * 110, rows * 130), (235, 235, 235, 255))
    draw = ImageDraw.Draw(sheet)
    for index, (action, image) in enumerate(thumbs):
        x = index % cols * 110
        y = index // cols * 130
        sheet.alpha_composite(image, (x + (110 - image.width) // 2, y + 2))
        draw.text((x + 2, y + 108), f"{action[:14]} {index}", fill=(0, 0, 0, 255))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.convert("RGB").save(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="request JSON")
    parser.add_argument("--output", required=True, help="run output directory")
    parser.add_argument("--python", default=sys.executable, help="Python used for process_webm.py")
    parser.add_argument("--process-script", help="override process_webm.py path")
    parser.add_argument("--install-dir", "--pet-dir", dest="install_dir", help="install completed V1 pet here (overrides config.install_dir/pet_dir)")
    parser.add_argument("--overwrite", action="store_true", help="allow writing into a non-empty install directory")
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    request = load_request(config_path)
    python = Path(args.python).expanduser().resolve()
    process_script = Path(args.process_script).expanduser().resolve() if args.process_script else Path(__file__).with_name("process_webm.py")
    if not process_script.exists():
        die(f"process script does not exist: {process_script}")

    output.mkdir(parents=True, exist_ok=True)
    (output / "request.normalized.json").write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    work = output / "work"
    actions_root = output / "actions"
    sources = request.get("sources", {})
    results: dict[str, dict] = {}
    contact_dirs: list[tuple[str, Path]] = []
    action_dirs: dict[str, Path] = {}
    install_requested = bool(args.install_dir or request.get("install_dir") or request.get("pet_dir"))

    # Process real sources first so mirror(running-right) works regardless of
    # the order in which the model emitted the JSON keys.
    action_items = list(request["actions"].items())
    action_items.sort(key=lambda item: str(item[1]).startswith("mirror("))
    for action, spec in action_items:
        if not isinstance(action, str) or not action.strip():
            die("action names must be non-empty strings")
        source, mirror = resolve_source(action, spec, sources, config_path.parent)
        action_work = work / action
        selected_dir = actions_root / action / "frames"
        if mirror:
            source_action = str(spec)[7:-1]
            source_selected = actions_root / source_action / "frames"
            files = frame_files(source_selected)
            if not files:
                results[action] = {"status": "blocked", "error": f"mirror source not processed: {source_action}"}
                continue
            selected = []
            selected_dir.mkdir(parents=True, exist_ok=True)
            for index, path in enumerate(files):
                with Image.open(path) as opened:
                    flipped = opened.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                    target = selected_dir / f"frame-{index:04d}.png"
                    flipped.save(target)
                    selected.append(str(target))
            results[action] = {"status": "mirrored", "source_action": source_action, "frames": len(selected)}
            action_dirs[action] = selected_dir
            contact_dirs.append((action, selected_dir))
            continue
        if source is None:
            results[action] = {"status": "missing", "error": "no source path supplied"}
            continue
        result = process_webm(python, process_script, source, action_work, request["key"], request["fps"])
        if result.get("status") not in ("processed",):
            results[action] = result
            continue
        requested_count = STANDARD_COUNTS.get(action, int(request["frames_per_action"])) if install_requested else int(request["frames_per_action"])
        files = select_frames(frame_files(action_work / "frames"), requested_count)
        selected_paths = copy_selected(files, selected_dir)
        action_dirs[action] = selected_dir
        results[action] = {
            "status": "ready",
            "source": str(source),
            "source_frames": len(frame_files(action_work / "frames")),
            "selected_frames": len(selected_paths),
            "frames": selected_paths,
        }
        contact_dirs.append((action, selected_dir))

    make_contact_sheet(contact_dirs, output / "contact-sheet.png")
    install_dir_value = args.install_dir or request.get("install_dir") or request.get("pet_dir")
    install_result = None
    if install_dir_value:
        install_dir = Path(install_dir_value).expanduser()
        if not install_dir.is_absolute():
            install_dir = (config_path.parent / install_dir).resolve()
        install_result = install_v1(request, output, install_dir, action_dirs, args.overwrite)
    report = {
        "operator": request["operator"],
        "costume": request["costume"],
        "version": request["version"],
        "cell": list(CELL),
        "frames_per_action": request["frames_per_action"],
        "actions": results,
        "install": install_result,
        "next_step": "V1 installed" if install_result and install_result.get("status") == "installed" else "assemble standard atlas when all required rows are ready; V2 also requires 16 look-direction cells",
    }
    (output / "build-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
