#!/usr/bin/env python3
"""Small Tkinter front-end for build_bitmap_pet.py."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


APP_NAME = "WEBMpets"
DEFAULT_PET_DIR = "~\\.codex\\pets\\"


def settings_path() -> Path:
    appdata = os.environ.get("APPDATA")
    root = Path(appdata) if appdata else Path.home() / ".config"
    return root / APP_NAME / "settings.json"


def load_settings() -> dict:
    path = settings_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(values: dict) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolved_pet_dir(pet_text: str, bitmap_dir: Path) -> Path:
    """Apply the pets-root append rule and return an absolute path."""
    raw = pet_text.strip().strip('"').replace("/", "\\") or DEFAULT_PET_DIR
    raw = raw.rstrip("\\/")
    # Path.name is the right test here: ``mypets`` must not be treated as the
    # Codex pets root merely because its string ends with the letters "pets".
    candidate = Path(raw).expanduser()
    if candidate.name.casefold() == "pets":
        candidate = candidate / bitmap_dir.name
    return candidate.resolve()


class PetBuilderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("WEBMpets 桌宠生成器")
        self.root.minsize(680, 260)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        saved = load_settings()
        self.bitmap_var = tk.StringVar(value=saved.get("bitmap_dir", ""))
        self.pet_var = tk.StringVar(value=saved.get("pet_dir", DEFAULT_PET_DIR))
        self.overwrite_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="请选择位图或 WEBM 素材目录。")
        self._build_widgets()
        self.root.after(100, self._poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _build_widgets(self) -> None:
        frame = ttk.Frame(self.root, padding=14)
        frame.grid(sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="bitmapdir：").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=self.bitmap_var).grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Button(frame, text="选择…", command=self._choose_bitmap).grid(row=0, column=2, padx=(8, 0))

        ttk.Label(frame, text="petdir：").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=self.pet_var).grid(row=1, column=1, sticky="ew", pady=6)
        ttk.Button(frame, text="选择…", command=self._choose_pet).grid(row=1, column=2, padx=(8, 0))

        ttk.Checkbutton(frame, text="允许覆盖非空宠物目录", variable=self.overwrite_var).grid(row=2, column=1, sticky="w", pady=4)
        ttk.Label(frame, textvariable=self.status_var, foreground="#555", wraplength=640).grid(row=3, column=0, columnspan=3, sticky="w", pady=(12, 8))
        self.generate_button = ttk.Button(frame, text="生成桌宠", command=self._generate)
        self.generate_button.grid(row=4, column=1, sticky="e", pady=8)

    def _choose_bitmap(self) -> None:
        selected = filedialog.askdirectory(title="选择 bitmapdir")
        if selected:
            self.bitmap_var.set(str(Path(selected).expanduser().resolve()))

    def _choose_pet(self) -> None:
        selected = filedialog.askdirectory(title="选择 petdir")
        if selected:
            self.pet_var.set(str(Path(selected).expanduser().resolve()))

    def _worker_command(self, bitmap_dir: Path, pet_dir: Path) -> list[str]:
        base = Path(sys.executable).resolve().parent
        script = Path(__file__).resolve().parent / "prts-spine-pet" / "scripts" / "build_bitmap_pet.py"
        if getattr(sys, "frozen", False):
            worker = base / "build_bitmap_pet.exe"
            if not worker.exists():
                raise FileNotFoundError(f"未找到打包后的主程序：{worker}")
            return [str(worker), "--bitmap-dir", str(bitmap_dir), "--pet-dir", str(pet_dir)]
        return [sys.executable, str(script), "--bitmap-dir", str(bitmap_dir), "--pet-dir", str(pet_dir)]

    def _generate(self) -> None:
        bitmap_text = self.bitmap_var.get().strip().strip('"')
        if not bitmap_text:
            messagebox.showerror("缺少 bitmapdir", "请选择素材目录。")
            return
        bitmap_dir = Path(bitmap_text).expanduser().resolve()
        if not bitmap_dir.is_dir():
            messagebox.showerror("目录不存在", f"bitmapdir 不存在：\n{bitmap_dir}")
            return
        # Normalize the visible value too; Tkinter may return forward slashes
        # even though Windows accepts and internally resolves both forms.
        self.bitmap_var.set(str(bitmap_dir))
        try:
            pet_dir = resolved_pet_dir(self.pet_var.get(), bitmap_dir)
            command = self._worker_command(bitmap_dir, pet_dir)
        except (OSError, ValueError) as exc:
            messagebox.showerror("路径错误", str(exc))
            return
        if self.overwrite_var.get():
            command.append("--overwrite")
        save_settings({"bitmap_dir": str(bitmap_dir), "pet_dir": self.pet_var.get().strip() or DEFAULT_PET_DIR})
        self.status_var.set(f"目标目录：{pet_dir}\n正在生成，请稍候…")
        self.generate_button.configure(state="disabled")
        threading.Thread(target=self._run_worker, args=(command, pet_dir), daemon=True).start()

    def _run_worker(self, command: list[str], pet_dir: Path) -> None:
        try:
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
            output = completed.stdout.strip() or completed.stderr.strip()
            self.events.put(("done", (completed.returncode, output, pet_dir)))
        except OSError as exc:
            self.events.put(("error", str(exc)))

    def _poll_events(self) -> None:
        try:
            kind, value = self.events.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll_events)
            return
        self.generate_button.configure(state="normal")
        if kind == "error":
            self.status_var.set("生成失败。")
            messagebox.showerror("生成失败", str(value))
        else:
            code, output, pet_dir = value
            if code == 0 and '"status": "installed"' in output:
                self.status_var.set(f"生成完成：{pet_dir}")
                messagebox.showinfo("生成完成", f"桌宠已写入：\n{pet_dir}")
            else:
                self.status_var.set("生成未完成，请查看输出信息。")
                messagebox.showerror("生成未完成", output[-4000:] or f"退出码：{code}")
        self.root.after(100, self._poll_events)

    def _close(self) -> None:
        save_settings({"bitmap_dir": self.bitmap_var.get().strip(), "pet_dir": self.pet_var.get().strip() or DEFAULT_PET_DIR})
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    PetBuilderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
