"""Run by the build scripts before PyInstaller: is this environment really the CPU / GPU flavour being built?

CPU and GPU builds of onnxruntime / paddle share module names, so one environment holds exactly one flavour. Building
the wrong flavour would silently bundle the wrong libraries (or a GPU exe that cannot use the GPU), so it stops here.
"""
from __future__ import annotations

import importlib.metadata as md
import re
import sys


def version(name: str) -> str | None:
    try:
        return md.version(name)
    except md.PackageNotFoundError:
        return None


def main(flavor: str) -> int:
    problems: list[str] = []
    notes: list[str] = []
    if sys.version_info[:2] != (3, 12):
        notes.append(f"Python {sys.version.split()[0]}: 3.12 is recommended (newer versions have no paddlepaddle wheel).")
    if version("pyinstaller") is None:
        problems.append("PyInstaller is not installed:  pip install -r requirements-build.txt")

    cpu_paddle, gpu_paddle = version("paddlepaddle"), version("paddlepaddle-gpu")
    cpu_ort, gpu_ort, dml_ort = version("onnxruntime"), version("onnxruntime-gpu"), version("onnxruntime-directml")
    if flavor == "cpu":
        if not cpu_paddle:
            problems.append("paddlepaddle (CPU) is not installed.")
        if gpu_paddle:
            problems.append(f"paddlepaddle-gpu {gpu_paddle} is installed: this is a GPU environment, not a CPU one.")
        if not cpu_ort:
            problems.append("onnxruntime (CPU) is not installed.")
        for name, v in (("onnxruntime-gpu", gpu_ort), ("onnxruntime-directml", dml_ort)):
            if v:
                problems.append(f"{name} {v} is installed: it would shadow the CPU onnxruntime.")
        if cpu_paddle and re.match(r"3\.3\.", cpu_paddle):
            notes.append(f"paddlepaddle {cpu_paddle} (CPU) has a known inference bug on some CPUs "
                         "(ConvertPirAttribute2RuntimeAttribute). requirements-cpu.txt pins 3.2.2.")
    else:
        if not gpu_paddle:
            problems.append("paddlepaddle-gpu is not installed (run install_gpu.ps1).")
        if cpu_paddle:
            problems.append(f"paddlepaddle (CPU) {cpu_paddle} is also installed and shadows paddlepaddle-gpu.")
        if not gpu_ort:
            problems.append("onnxruntime-gpu is not installed (run install_gpu.ps1).")
        for name, v in (("onnxruntime", cpu_ort), ("onnxruntime-directml", dml_ort)):
            if v:
                problems.append(f"{name} {v} is also installed and shadows onnxruntime-gpu.")
    for name in ("paddleocr", "paddlex", "pykokoro", "PyQt6", "pymupdf"):
        if not version(name):
            problems.append(f"{name} is not installed.")

    print(f"[check_env] flavour={flavor}  paddle={gpu_paddle or cpu_paddle}  onnxruntime={gpu_ort or cpu_ort or dml_ort}")
    for n in notes:
        print(f"[check_env] note: {n}")
    for p in problems:
        print(f"[check_env] PROBLEM: {p}")
    if problems:
        print(f"[check_env] This environment is not a clean {flavor.upper()} install. Use a fresh one "
              f"(build_{flavor.upper()}.bat creates .build-{flavor} for you), or fix the items above.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main((sys.argv[1] if len(sys.argv) > 1 else "cpu").lower()))
