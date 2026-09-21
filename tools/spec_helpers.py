"""Helpers for echoread.spec. Each one exists because of a way a PyInstaller build silently loses something:

* dependency_closure()  – PaddleX/transformers-style libraries read `importlib.metadata` (the *.dist-info folders), which
                          PyInstaller does not copy unless told to: "requires additional dependencies" although installed.
* top_level_modules()   – modules that are imported lazily (PaddleOCR) are invisible to PyInstaller's import scan.
* native_libraries()    – DLLs/.pyd/.so that a library finds *by file path* are never "imported", so never bundled.
"""
from __future__ import annotations

import importlib.metadata as md
import importlib.util
import re
from pathlib import Path

NATIVE_SUFFIXES = (".dll", ".pyd", ".so", ".dylib")
SKIP_MODULES = {"pip", "wheel", "setuptools", "distutils", "tests", "test", "docs", "examples", "benchmarks", "_distutils_hack"}


def canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def is_installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _installed_distributions() -> dict[str, md.Distribution]:
    out: dict[str, md.Distribution] = {}
    for dist in md.distributions():
        name = dist.metadata["Name"] if dist.metadata else None
        if name:
            out.setdefault(canon(name), dist)
    return out


def dependency_closure(roots: list[str]) -> list[str]:
    """Names of every installed distribution reachable from `roots`, extras included ("paddlex[ocr,ocr-core]")."""
    from packaging.requirements import InvalidRequirement, Requirement

    installed = _installed_distributions()
    seen: set[str] = set()
    visited: set[tuple[str, tuple[str, ...]]] = set()
    stack: list[tuple[str, tuple[str, ...]]] = []
    for root in roots:
        try:
            req = Requirement(root)
        except InvalidRequirement:
            continue
        stack.append((canon(req.name), tuple(sorted(req.extras))))
    while stack:
        name, extras = stack.pop()
        if (name, extras) in visited:
            continue
        visited.add((name, extras))
        dist = installed.get(name)
        if dist is None:
            continue
        seen.add(name)
        for spec in dist.requires or []:
            try:
                req = Requirement(spec)
            except InvalidRequirement:
                continue
            if req.marker is not None and not any(req.marker.evaluate({"extra": e}) for e in ("", *extras)):
                continue
            stack.append((canon(req.name), tuple(sorted(req.extras))))
    return sorted(seen)


def top_level_modules(dist_names: list[str]) -> list[str]:
    """Importable top-level module names provided by the given distributions."""
    installed = _installed_distributions()
    modules: set[str] = set()
    for name in dist_names:
        dist = installed.get(canon(name))
        if dist is None:
            continue
        text = dist.read_text("top_level.txt")
        names = text.split() if text else []
        if not names:
            for f in dist.files or []:
                top = f.parts[0] if f.parts else ""
                if top.endswith(".py"):
                    top = top[:-3]
                if top and not top.startswith(("_", ".")) and ".dist-info" not in top and "__pycache__" not in top:
                    names.append(top)
        for n in names:
            n = n.split("/")[0]
            if n.isidentifier() and n not in SKIP_MODULES and not n.startswith("__"):
                modules.add(n)
    return sorted(modules)


def native_libraries(package: str, extra_libs: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """(source file, destination folder) for every native library inside a package, keeping its folder layout.
    Also picks up the sibling '<package>.libs' folder that delvewheel/auditwheel-built wheels use, and any `extra_libs` folders
    next to the package (pyopenjtalk-plus ships its DLLs in 'pyopenjtalk_plus.libs', not 'pyopenjtalk.libs')."""
    try:
        spec = importlib.util.find_spec(package)
    except (ImportError, ValueError):
        return []
    if spec is None:
        return []
    if spec.submodule_search_locations is None:
        return []  # a single-file module (sounddevice.py): its parent folder is site-packages, which must NOT be scanned
    dirs = [Path(p) for p in spec.submodule_search_locations]
    found: list[tuple[str, str]] = []
    for base in dirs:
        site = base.parent
        roots = [base]
        for name in (f"{base.name}.libs", *extra_libs):
            sibling = site / name
            if sibling.is_dir() and sibling not in roots:
                roots.append(sibling)
        for root in roots:
            for f in root.rglob("*"):
                if f.is_file() and (f.suffix.lower() in NATIVE_SUFFIXES or ".so." in f.name):
                    found.append((str(f), str(f.parent.relative_to(site))))
    return found


DYNAMIC_IMPORT_RE = re.compile(r"import_module\(|__import__\(|iter_modules\(|walk_packages\(|spec_from_file_location\(")


def python_files(module: str) -> list[Path]:
    """The .py files of a top-level package ([] for a single-file module or anything not installed)."""
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, ValueError):
        return []
    if spec is None or spec.submodule_search_locations is None:
        return []
    files: list[Path] = []
    for base in spec.submodule_search_locations:
        files += [f for f in Path(base).rglob("*.py") if "__pycache__" not in f.parts]
    return files


def has_pyinstaller_hook(module: str) -> bool:
    """True if PyInstaller (or pyinstaller-hooks-contrib) ships a hook for this package, i.e. someone already handles it."""
    roots: list[Path] = []
    for name in ("PyInstaller.hooks", "_pyinstaller_hooks_contrib"):
        spec = importlib.util.find_spec(name)
        if spec and spec.submodule_search_locations:
            roots += [Path(p) for p in spec.submodule_search_locations]
    return any(next(root.rglob(f"hook-{module}.py"), None) is not None or next(root.rglob(f"hook-{module}.*.py"), None) is not None
               for root in roots)


def dynamic_import_packages(dist_names: list[str], max_files: int = 250) -> list[str]:
    """Top-level packages (from the given distributions) whose own source imports modules BY NAME at run time
    (importlib.import_module, __import__, pkgutil.iter_modules …). PyInstaller's import scan cannot see those, so every
    submodule of such a package must be collected explicitly, e.g. numeralform's `import_module(".is")` (a module called
    `is`, which cannot be written as a normal import). Packages that already have a PyInstaller hook (numpy, cv2, PIL,
    pydantic, spacy…) are skipped: they are handled, and collecting them whole would only bloat the build."""
    found = []
    for module in top_level_modules(dist_names):
        files = python_files(module)
        if not files or len(files) > max_files or has_pyinstaller_hook(module):
            continue
        for f in files:
            try:
                if DYNAMIC_IMPORT_RE.search(f.read_text("utf-8", errors="ignore")):
                    found.append(module)
                    break
            except OSError:
                continue
    return sorted(found)