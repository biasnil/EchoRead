"""Run:  python gpu_check.py     Tells you whether EchoRead's speech and OCR can really use your GPU, and what's missing."""
from __future__ import annotations

import sys

from core.gpu import CudaLibraries


def main() -> int:
    print(f"Python {sys.version.split()[0]}\n")
    try:
        import onnxruntime as ort
    except ImportError:
        print("onnxruntime is not installed.  pip install -r requirements-gpu.txt")
        return 1
    print(f"onnxruntime {ort.__version__}   providers it lists: {ort.get_available_providers()}")
    if "CUDAExecutionProvider" not in ort.get_available_providers():
        print("  -> This is the CPU build (or a mix of builds). For the GPU:  pip uninstall -y onnxruntime onnxruntime-gpu"
              "  then  pip install onnxruntime-gpu")

    cuda = CudaLibraries()
    print("\nCUDA 13 / cuDNN 9 libraries ONNX Runtime needs:")
    missing = []
    for name, folder in cuda.locate().items():
        print(f"  {'found  ' if folder else 'MISSING'}  {name:<18} {folder or ''}")
        if not folder:
            missing.append(name)
    cuda.prepare()

    ok, providers, error = cuda.check_session()
    print("\nSpeech (ONNX Runtime) GPU test:")
    if ok:
        print(f"  GPU WORKS  — the session got {providers}")
    else:
        print("  NOT on the GPU.", (error.splitlines() or [""])[0][:300] if error else f"Session fell back to {providers}")
        if any(n.startswith("cudnn") for n in missing):
            print("  -> cuDNN 9 for CUDA 13 is missing: install it from NVIDIA (add its bin folder to PATH) or  pip install nvidia-cudnn-cu13")
        if any(n.startswith(("cudart", "cublas", "cufft")) for n in missing):
            print("  -> CUDA 13 runtime libraries are missing: install the CUDA 13 toolkit (or  pip install nvidia-cuda-runtime nvidia-cublas nvidia-cufft)")

    print("\nOCR (PaddlePaddle):")
    try:
        import paddle

        gpu_build = paddle.device.is_compiled_with_cuda()
        count = paddle.device.cuda.device_count() if gpu_build else 0
        print(f"  paddle {paddle.__version__}: {'GPU build' if gpu_build else 'CPU build'}; GPUs it can use: {count}")
        if not gpu_build:
            print("  -> OCR runs on the CPU. For the GPU:  pip uninstall -y paddlepaddle   then   python -m pip install "
                  "paddlepaddle-gpu==3.3.0 -i https://www.paddlepaddle.org.cn/packages/stable/cu130/")
    except ImportError:
        print("  PaddlePaddle is not installed.")
    print("\nIn EchoRead, Settings -> Device -> GPU, then restart it.")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
