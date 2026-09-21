@echo off
REM EchoRead CPU build  ->  dist\EchoRead-CPU\EchoRead-CPU.exe
REM Options:  --clean  (fresh build)   --debug  (console window)   --current  (use the activated venv)
call "%~dp0tools\build_flavor.bat" cpu %*
exit /b %errorlevel%
