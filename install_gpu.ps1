# EchoRead — NVIDIA GPU install (PowerShell). Run from the EchoRead folder with your venv activated:
#     .\install_gpu.ps1
# This is the sequence that worked on the developer's machine (CUDA 13 toolkit + cuDNN 9, Windows, Python 3.12).
# It exists because two GPU packages come from their own servers, which a plain requirements file can't express.
#
# Before running:  install the NVIDIA driver, the CUDA 13 toolkit and cuDNN 9 for CUDA 13
#                  (or  pip install nvidia-cudnn-cu13  for cuDNN).  EchoRead finds the CUDA libraries itself.
# If you get a script-execution error:   Set-ExecutionPolicy -Scope Process Bypass    then run it again.

$ErrorActionPreference = "Stop"

# true  = the nightly CUDA 13 build of onnxruntime-gpu (what was confirmed working).
# false = the released onnxruntime-gpu from PyPI (also a CUDA 13 build from 1.27 on). Not yet tried on this machine;
#         it may work now that EchoRead locates the CUDA/cuDNN libraries itself.
$OrtNightly = $true
$NightlyIndex = "https://aiinfra.pkgs.visualstudio.com/PublicPackages/_packaging/ort-cuda-13-nightly/pypi/simple/"
$PaddleIndex  = "https://www.paddlepaddle.org.cn/packages/stable/cu130/"

function Run($what, [scriptblock]$cmd) {
    Write-Host "`n==> $what" -ForegroundColor Cyan
    & $cmd
    if ($LASTEXITCODE -ne 0) { throw "Step failed: $what" }
}

# 1. Remove every flavour of these packages: they share module names, so leftovers (e.g. DirectML) shadow each other.
#    (pip only warns about ones that aren't installed.)
Run "Removing old onnxruntime / paddlepaddle builds" {
    python -m pip uninstall -y onnxruntime onnxruntime-directml onnxruntime-gpu paddlepaddle paddlepaddle-gpu
}

# 2. Everything that comes from PyPI.
Run "Installing the shared packages" { python -m pip install -r requirements-base.txt }

# 3. onnxruntime-gpu. The nightly feed replaces PyPI for that command, so its dependencies are installed first.
Run "Installing onnxruntime-gpu's dependencies" {
    python -m pip install coloredlogs flatbuffers numpy packaging protobuf sympy
}
if ($OrtNightly) {
    Run "Installing onnxruntime-gpu (CUDA 13 nightly)" {
        python -m pip install --pre --index-url $NightlyIndex onnxruntime-gpu
    }
} else {
    Run "Installing onnxruntime-gpu (released CUDA 13 build)" { python -m pip install onnxruntime-gpu }
}

# 4. PaddlePaddle GPU for OCR (CUDA 13.0 build, from Paddle's server). Step 1 removed every Paddle, so if this fails
#    the CPU build goes back: OCR must never be left with no PaddlePaddle at all.
Write-Host "`n==> Installing paddlepaddle-gpu" -ForegroundColor Cyan
python -m pip install paddlepaddle-gpu==3.3.0 -i $PaddleIndex
if ($LASTEXITCODE -ne 0) {
    Write-Host "Paddle GPU could not be installed - putting the CPU build back so OCR still works." -ForegroundColor Yellow
    python -m pip install paddlepaddle
    if ($LASTEXITCODE -ne 0) { throw "Could not install any PaddlePaddle build" }
}

# 5. Show what is installed, then check the GPU really works.
Run "Installed versions" { python -m pip list --format=freeze | Select-String -Pattern "onnxruntime|paddle|nvidia" }
Write-Host "`n==> Checking the GPU" -ForegroundColor Cyan
python gpu_check.py
Write-Host "`nDone. In EchoRead: Settings -> Device -> GPU, then restart." -ForegroundColor Green
Write-Host "Tip: 'pip freeze > requirements-working.txt' saves the exact versions that work." -ForegroundColor Green
