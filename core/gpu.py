"""Finding the CUDA 13 / cuDNN 9 libraries that ONNX Runtime's GPU build needs on Windows, and checking the GPU works.

The CUDA 13 toolkit keeps its DLLs in a sub-folder (bin\\x64) that its installer doesn't always put on PATH, and cuDNN is a
separate install, so "CUDA is installed" doesn't mean ONNX Runtime can find the libraries. This looks in the usual places
(the toolkit, the cuDNN installer, pip's nvidia packages, PATH) and makes what it finds visible to this process.
"""
from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import os
import sys
from pathlib import Path

# a 142-byte ONNX model with one Conv node: on the CUDA provider a Conv needs cuDNN, so running it proves the whole chain
_CONV_MODEL = "CAg6gwEKJgoBWAoBVxIBWSIEQ29udioVCgxrZXJuZWxfc2hhcGVAAUABoAEHEgpjb252X2NoZWNrKhMIAQgBCAEIARABQgFXSgQAAIA/WhsKAVgSFgoUCAESEAoCCAEKAggBCgIIBAoCCARiGwoBWRIWChQIARIQCgIIAQoCCAEKAggECgIIBEIECgAQDQ=="


class CudaLibraries:
    WANTED = ("cudart64_13.dll", "cublas64_13.dll", "cublasLt64_13.dll", "cufft64_12.dll", "cudnn64_9.dll")

    def __init__(self, roots: list[Path] | None = None, windows: bool | None = None):
        """`roots` (folders to search) and `windows` can be given for tests."""
        self._roots = roots
        self._windows = (os.name == "nt") if windows is None else windows
        self._handles: list = []
        self.found: dict[str, str | None] = {}
        self.preload_log = ""

    # -- where to look
    def _search_roots(self) -> list[Path]:
        if self._roots is not None:
            return [Path(r) for r in self._roots]
        roots: list[Path] = []
        if getattr(sys, "frozen", False):  # a GPU build carries its own NVIDIA libraries (pip's nvidia-* packages)
            roots.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "nvidia")
        program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        for key, value in os.environ.items():
            if key == "CUDA_PATH" or key.startswith("CUDA_PATH_V13"):
                roots.append(Path(value))
        roots += sorted((program_files / "NVIDIA GPU Computing Toolkit" / "CUDA").glob("v13*"), reverse=True)
        roots += sorted((program_files / "NVIDIA" / "CUDNN").glob("v9*"), reverse=True)
        spec = importlib.util.find_spec("nvidia")  # pip's nvidia-* packages
        roots += [Path(p) for p in (spec.submodule_search_locations or [])] if spec else []
        return roots

    def locate(self) -> dict[str, str | None]:
        """DLL name -> the folder it was found in (None if nowhere)."""
        roots = [r for r in self._search_roots() if r.exists()]
        path_dirs = [Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
        found: dict[str, str | None] = {}
        for name in self.WANTED:
            hit = next((str(d) for d in path_dirs if (d / name).exists()), None)
            if hit is None:
                for root in roots:
                    match = next(iter(root.rglob(name)), None)
                    if match is not None:
                        hit = str(match.parent)
                        break
            found[name] = hit
        self.found = found
        return found

    # -- make them visible to this process
    def prepare(self) -> None:
        if not self._windows:
            return
        found = self.locate()
        path = os.environ.get("PATH", "")
        for folder in dict.fromkeys(f for f in found.values() if f):
            if hasattr(os, "add_dll_directory"):
                try:
                    self._handles.append(os.add_dll_directory(folder))
                except OSError:
                    pass
            if folder not in path.split(os.pathsep):
                path = folder + os.pathsep + path
        os.environ["PATH"] = path
        self._preload(found)

    def _preload(self, found: dict[str, str | None]) -> None:
        """ONNX Runtime's own preload (ORT 1.21+), pointed at the folders we found. Its 'Failed to load…' chatter is captured."""
        buffer = io.StringIO()
        try:
            import onnxruntime

            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                onnxruntime.preload_dlls()
                cuda_dir, cudnn_dir = found.get("cudart64_13.dll"), found.get("cudnn64_9.dll")
                if cuda_dir:
                    onnxruntime.preload_dlls(cuda=True, cudnn=False, directory=cuda_dir)
                if cudnn_dir:
                    onnxruntime.preload_dlls(cuda=False, cudnn=True, directory=cudnn_dir)
        except Exception as exc:  # older ORT, or not the GPU build
            buffer.write(f"{exc}\n")
        self.preload_log = buffer.getvalue()

    # -- does it actually work?
    @staticmethod
    def check_session() -> tuple[bool, list[str], str]:
        """(runs on the GPU?, providers the session really got, error text). Uses a Conv so cuDNN is required."""
        import numpy as np
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.log_severity_level = 3
        try:
            session = ort.InferenceSession(base64.b64decode(_CONV_MODEL), sess_options=options,
                                           providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
            session.run(None, {"X": np.ones((1, 1, 4, 4), np.float32)})
        except Exception as exc:
            return False, [], str(exc).strip()
        providers = session.get_providers()
        return "CUDAExecutionProvider" in providers, providers, ""
