@echo off
REM ============================================================================================================
REM  EchoRead build worker.  Use build_CPU.bat / build_GPU.bat instead of calling this directly.
REM      tools\build_flavor.bat <cpu|gpu> [--clean] [--debug] [--current]
REM  --clean    delete the previous build first (do this after changing echoread.spec or the requirements)
REM  --debug    keep a console window in the exe so you can see its output (a windowed exe has none)
REM  --current  use the virtual environment that is already activated instead of creating .build-<flavour>
REM ============================================================================================================
setlocal EnableExtensions EnableDelayedExpansion

set "FLAVOR=%~1"
if /I "%FLAVOR%"=="cpu" (set "UP=CPU") else if /I "%FLAVOR%"=="gpu" (set "UP=GPU") else (
  echo Usage: tools\build_flavor.bat ^<cpu^|gpu^> [--clean] [--debug] [--current]
  exit /b 2
)
set "FLAVOR=%FLAVOR%"
set "CLEAN=" & set "DEBUG=" & set "CURRENT="
for %%A in (%*) do (
  if /I "%%~A"=="--clean"   set "CLEAN=1"
  if /I "%%~A"=="--debug"   set "DEBUG=1"
  if /I "%%~A"=="--current" set "CURRENT=1"
)

cd /d "%~dp0.."
set "NAME=EchoRead-%UP%"
set "OUT=dist\%NAME%"
echo.
echo ===== EchoRead %UP% build =====

REM ---- 1. which Python / virtual environment
if defined CURRENT (
  if not defined VIRTUAL_ENV (
    echo --current needs an activated virtual environment ^(run venv\Scripts\activate first^).
    exit /b 2
  )
  set "PY=python"
) else (
  set "VENV=.build-%FLAVOR%"
  if not exist "!VENV!\Scripts\python.exe" (
    echo Creating !VENV! ^(Python 3.12 recommended^)...
    py -3.12 -m venv "!VENV!" 2>nul || python -m venv "!VENV!" || (
      echo Could not create the virtual environment. Install Python 3.12 and try again.
      exit /b 1
    )
  )
  set "PY=!VENV!\Scripts\python.exe"
  set "PATH=!CD!\!VENV!\Scripts;!PATH!"
)

REM ---- 2. install the packages for this flavour (once; --clean redoes it)
if not defined CURRENT (
  set "MARK=!VENV!\.echoread-%FLAVOR%-installed"
  if defined CLEAN if exist "!MARK!" del "!MARK!"
  if not exist "!MARK!" (
    "!PY!" -m pip install --upgrade pip || exit /b 1
    if /I "%FLAVOR%"=="cpu" (
      "!PY!" -m pip install -r requirements-cpu.txt || exit /b 1
    ) else (
      powershell -NoProfile -ExecutionPolicy Bypass -File install_gpu.ps1 || exit /b 1
    )
    echo installed> "!MARK!"
  )
)
"!PY!" -m pip install -r requirements-build.txt || exit /b 1

REM ---- 3. is this really the right flavour? (CPU and GPU builds of onnxruntime/paddle cannot mix)
"!PY!" tools\check_env.py %FLAVOR% || exit /b 1

REM ---- 4. build
if defined CLEAN (
  if exist "build\%FLAVOR%" rmdir /s /q "build\%FLAVOR%"
  if exist "%OUT%" rmdir /s /q "%OUT%"
  set "PYI_CLEAN=--clean"
) else (
  set "PYI_CLEAN="
)
set "ECHOREAD_FLAVOR=%FLAVOR%"
if defined DEBUG (set "ECHOREAD_DEBUG=1") else (set "ECHOREAD_DEBUG=0")
echo.
echo Building %NAME% ^(this takes several minutes; the GPU build is several GB^)...
"!PY!" -m PyInstaller echoread.spec --noconfirm !PYI_CLEAN! --workpath "build\%FLAVOR%" --distpath dist || (
  echo.
  echo PyInstaller failed - see the messages above.
  exit /b 1
)

REM ---- 5. self-test the finished exe (proves metadata, DLLs, data files and hidden imports made it in)
echo.
echo Running the build's self-test...
if exist "%OUT%\selftest_report.txt" del "%OUT%\selftest_report.txt"
start /wait "" "%OUT%\%NAME%.exe" --selftest
set "RC=!ERRORLEVEL!"
if exist "%OUT%\selftest_report.txt" (type "%OUT%\selftest_report.txt") else (echo ^(the exe wrote no report - see %%APPDATA%%\EchoRead\logs^))
if not "!RC!"=="0" (
  echo.
  echo *** SELF-TEST FAILED. The build is NOT good. Read the FAIL lines above; rebuild with --clean --debug to see a console.
  exit /b 1
)

echo.
echo ===== Done: %OUT%\%NAME%.exe =====
powershell -NoProfile -Command "'Folder size: {0:N0} MB' -f ((Get-ChildItem '%OUT%' -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)"
echo Copy the whole folder %OUT% to move the app. Logs: %%APPDATA%%\EchoRead\logs
exit /b 0
