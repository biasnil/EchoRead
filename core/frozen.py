"""Things a PyInstaller (frozen, windowed) build needs before anything else runs. Harmless when running from source.

A windowed exe has NO stdout/stderr: libraries that print (Paddle, ONNX Runtime, our own tracebacks) would crash or vanish,
so output goes to a log file; a crash shows a message box that says where the log is. Bundled DLL sub-folders (the NVIDIA
libraries in a GPU build) are not on Windows' DLL search path by themselves, so they are added here.
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Where bundled data lives (assets/…): PyInstaller's folder when frozen, the project folder otherwise."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def log_dir() -> Path:
    from .paths import AppPaths

    return AppPaths.default_root() / "logs"


def log_path() -> Path:
    """The structured, rotating log written by core.errors (time, level, module, traceback)."""
    return log_dir() / "echoread.log"


def console_log_path() -> Path:
    """Raw stdout/stderr of a windowed exe (library chatter, prints). Kept apart from echoread.log so that file can rotate."""
    return log_dir() / "console.log"


def prepare_runtime() -> None:
    """Call first thing in main.py."""
    import multiprocessing

    multiprocessing.freeze_support()  # needed on Windows if any library starts worker processes
    if not is_frozen():
        return
    windowed = sys.stdout is None or sys.stderr is None
    _install_streams()
    _install_excepthook(windowed)
    _add_dll_directories()


def _install_streams() -> None:
    if sys.stdout is not None and sys.stderr is not None:
        return  # a console (debug) build: leave the console alone
    try:
        path = console_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "w" if path.exists() and path.stat().st_size > 2_000_000 else "a"
        log = open(path, mode, buffering=1, encoding="utf-8", errors="replace")
        log.write(f"\n=== EchoRead started {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    except OSError:
        log = open(os.devnull, "w")
    os.environ.setdefault("NO_COLOR", "1")  # PaddleX's coloured logging would otherwise write [32m…[0m into the file
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


def _install_excepthook(windowed: bool) -> None:
    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            print(text, file=sys.stderr)
        except Exception:
            pass
        if windowed and os.name == "nt":
            try:
                import ctypes

                ctypes.windll.user32.MessageBoxW(
                    0, f"EchoRead hit an unexpected error and has to close.\n\n{exc_type.__name__}: {exc}\n\n"
                       f"Details were saved in:\n{log_dir()}", "EchoRead", 0x10)
            except Exception:
                pass

    sys.excepthook = hook


def _add_dll_directories() -> None:
    """Bundled DLL folders that Windows would not search on its own (Paddle's libs, ONNX providers, NVIDIA CUDA/cuDNN)."""
    root = resource_root()
    folders = [root, root / "paddle" / "libs", root / "onnxruntime" / "capi"]
    nvidia = root / "nvidia"
    if nvidia.is_dir():
        folders += sorted({p.parent for p in nvidia.rglob("*.dll")})
    path = os.environ.get("PATH", "")
    for folder in folders:
        if not folder.is_dir():
            continue
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(str(folder))
            except OSError:
                pass
        if str(folder) not in path.split(os.pathsep):
            path = str(folder) + os.pathsep + path
    os.environ["PATH"] = path
