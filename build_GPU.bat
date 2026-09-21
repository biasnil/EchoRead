@echo off
REM EchoRead GPU build (NVIDIA, CUDA 13)  ->  dist\EchoRead-GPU\EchoRead-GPU.exe
REM Options:  --clean  (fresh build)   --debug  (console window)   --current  (use the activated venv)
call "%~dp0tools\build_flavor.bat" gpu %*
exit /b %errorlevel%
