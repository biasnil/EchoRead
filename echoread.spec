# -*- mode: python ; coding: utf-8 -*-
# EchoRead PyInstaller spec — one spec, two flavours:
#     build_CPU.bat  ->  dist\EchoRead-CPU\EchoRead-CPU.exe
#     build_GPU.bat  ->  dist\EchoRead-GPU\EchoRead-GPU.exe
# Selected with the environment variables ECHOREAD_FLAVOR=cpu|gpu and ECHOREAD_DEBUG=1 (console window).
#
# Lessons built in (from a previous PyInstaller + Paddle project's BUILD_NOTES):
#   1. "Works from source, fails in the exe" = something the build did not copy: package metadata, native libraries
#      that are loaded by path, data files, lazily imported modules.  Each has its own section below.
#   2. Libraries read their own dist-info (PaddleX: "OCR requires additional dependencies" although installed), so the
#      metadata of the WHOLE dependency closure, extras included, is bundled, not a hand-kept list.
#   3. A windowed exe has no stdout/stderr: core/frozen.py sends output to %APPDATA%\EchoRead\logs\console.log
#      (core/errors.py writes the structured, rotating logs\echoread.log).
#   4. UPX stays OFF (it corrupts CUDA/Paddle DLLs). One-folder build (fast start-up).
#   5. `<exe> --selftest` proves the build; the build scripts run it automatically.
import json
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH)
sys.path.insert(0, str(ROOT / "tools"))
import spec_helpers as H  # noqa: E402

# The exe's icon. If assets/icon.ico is missing (it went missing once and the build stopped at the very last step), make it from
# assets/icons/icon.png (transparent) so the build never depends on a hand-made file.
ICON = ROOT / "assets" / "icon.ico"
if not ICON.exists():
    from PIL import Image

    Image.open(ROOT / "assets" / "icons" / "icon.png").convert("RGBA").save(
        ICON, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"[echoread.spec] assets/icon.ico was missing: made it from assets/icons/icon.png")

FLAVOR = os.environ.get("ECHOREAD_FLAVOR", "cpu").strip().lower()
if FLAVOR not in ("cpu", "gpu"):
    raise SystemExit(f"ECHOREAD_FLAVOR must be cpu or gpu, not {FLAVOR!r}")
GPU = FLAVOR == "gpu"
DEBUG = os.environ.get("ECHOREAD_DEBUG", "") == "1"
NAME = f"EchoRead-{FLAVOR.upper()}"


def note(msg):
    print(f"[echoread.spec] {msg}")


# ---------------------------------------------------------------- 1. package metadata (dist-info) for the whole closure
METADATA_ROOTS = [
    "paddleocr", "paddlex[ocr,ocr-core]", "paddlepaddle-gpu" if GPU else "paddlepaddle",
    "pykokoro", "kokorog2p[en,espeak]", "onnxruntime-gpu" if GPU else "onnxruntime", "espeakng-loader",
    "PyQt6", "pymupdf", "soundfile", "sounddevice", "requests", "readability-lxml", "lxml", "pillow", "numpy",
]
closure = H.dependency_closure(METADATA_ROOTS)
datas = []
for dist_name in closure:
    try:
        datas += copy_metadata(dist_name)
    except Exception as exc:  # a distribution without usable metadata: nothing to copy
        note(f"no metadata for {dist_name}: {exc}")
note(f"flavour={FLAVOR}: bundling dist-info for {len(closure)} distributions")

# ---------------------------------------------------------------- 2. data files
DATA_PACKAGES = [
    "paddlex", "paddleocr",                                   # pipeline configs (yaml), model registries
    "pykokoro", "kokorog2p", "lexphon", "phrasplit", "audiosig", "spokenform", "ssmd", "g2lex", "numeralform",
    "abbr2words", "cn2an", "proces",                          # Kokoro's text front-end: dictionaries, rules
    "espeakng_loader", "espeakng_runtime",                    # eSpeak NG data (espeak-ng-data/)
    "readability", "pypdfium2", "pypdfium2_raw", "imagesize", "en_core_web_sm",
    "jieba", "pypinyin", "pypinyin_dict", "pyopenjtalk",     # Chinese / Japanese voices: dictionaries (skipped when not installed)
]
for pkg in DATA_PACKAGES:
    if H.is_installed(pkg):
        datas += collect_data_files(pkg)
datas.append((str(ROOT / "assets"), "assets"))

# tells the running exe which flavour it is (used by --selftest)
try:
    from PyInstaller.config import CONF

    info_dir = Path(CONF["workpath"])
except Exception:  # not available in some PyInstaller versions
    info_dir = ROOT / "build" / FLAVOR
info_dir.mkdir(parents=True, exist_ok=True)
BUILD_INFO = info_dir / "build_info.json"   # written after section 4, once the module manifest is known
datas.append((str(BUILD_INFO), "."))

# ---------------------------------------------------------------- 3. native libraries that are found by path
NATIVE_PACKAGES = [
    "paddle", "onnxruntime", "espeakng_loader", "espeakng_runtime", "pymupdf", "fitz", "shapely", "cv2", "pyclipper",
    "pypdfium2", "pypdfium2_raw", "sounddevice", "_sounddevice_data", "soundfile", "_soundfile_data",
]
if GPU:
    NATIVE_PACKAGES.append("nvidia")  # pip's nvidia-* packages (cuDNN, cuBLAS, CUDA runtime…) that Paddle GPU depends on
binaries = []
for pkg in NATIVE_PACKAGES:
    libs = H.native_libraries(pkg)
    if libs:
        note(f"native libraries: {pkg}: {len(libs)} files")
    binaries += libs
# Japanese voices: pyopenjtalk-plus (Windows) keeps its DLLs in a sibling 'pyopenjtalk_plus.libs' folder and looks for it next to the package
_ja = H.native_libraries("pyopenjtalk", extra_libs=("pyopenjtalk_plus.libs",))
if _ja:
    note(f"native libraries: pyopenjtalk: {len(_ja)} files")
    binaries += _ja
if GPU and not H.native_libraries("nvidia"):
    note("WARNING: no pip 'nvidia-*' packages in this environment, so the GPU exe will rely on the CUDA 13 toolkit and "
         "cuDNN 9 installed on the machine it runs on.")

# ---------------------------------------------------------------- 4. lazily imported modules
# Some libraries import their own modules BY NAME at run time (numeralform: import_module(".is")), which PyInstaller's
# import scan cannot see. So: (a) a known list, plus (b) every package in Kokoro's/PaddleOCR's dependency tree whose
# source is found to do that, gets ALL its submodules collected.  The result is also written to build_info.json so that
# `--selftest` can check every one of those modules really is inside the finished build.
LAZY_ROOTS = ["pykokoro", "kokorog2p[en,espeak]", "paddleocr", "paddlex[ocr,ocr-core]"]
KNOWN_LAZY = ["paddlex", "paddleocr", "pykokoro", "kokorog2p", "lexphon", "phrasplit", "spokenform", "numeralform",
              "abbr2words", "cn2an", "proces", "ssmd", "g2lex", "audiosig", "espeakng_loader", "espeakng_runtime",
              "pyopenjtalk"]  # pyopenjtalk: its compiled .pyd imports pyopenjtalk._known_symbols by name, which PyInstaller cannot see
HEAVY = {"paddlex", "paddleocr", "paddle"}                # collected, but too big to import them all in the self-test
lazy_closure = H.dependency_closure(LAZY_ROOTS)
detected = H.dynamic_import_packages(lazy_closure)
lazy_packages = sorted(set(KNOWN_LAZY) | set(detected))
note(f"packages that import modules by name (auto-detected): {detected}")

hiddenimports = set(H.top_level_modules(H.dependency_closure(["paddleocr", "paddlex[ocr,ocr-core]"])))
manifest = set()
for pkg in lazy_packages:
    if not H.is_installed(pkg):
        continue
    subs = set(collect_submodules(pkg, filter=lambda n: ".tests" not in n and ".test_" not in n))
    hiddenimports |= subs
    if pkg not in HEAVY and len(H.python_files(pkg)) <= 250:
        manifest |= subs | {pkg}
hiddenimports |= {"core.selftest", "core.frozen", "PyQt6.QtSvg"}  # QtSvg draws the playback icons (assets/icons/*.svg)
note(f"hidden imports: {len(hiddenimports)}   (self-test will verify {len(manifest)} of them)")

BUILD_INFO.write_text(json.dumps({"flavor": FLAVOR, "name": NAME, "modules": sorted(manifest)}), "utf-8")

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(hiddenimports),
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "torch", "torchvision", "torchaudio", "tensorflow", "IPython", "notebook", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    debug=False,
    strip=False,
    upx=False,          # UPX corrupts torch/CUDA/Paddle DLLs
    console=DEBUG,      # windowed by default; --debug builds keep a console to see output
    icon=str(ICON),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=NAME)
