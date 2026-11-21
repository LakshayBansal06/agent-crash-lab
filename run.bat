@echo off
rem ======================================================================
rem  AGENT CRASH LAB - one-click launcher for Windows
rem
rem  Double-click this file (or type  run  in a terminal in this folder).
rem  It does everything:
rem    1. finds Python 3.11 or newer
rem    2. creates the private library box (.venv) the first time
rem    3. installs or updates the libraries only when they changed
rem    4. starts the server and opens the website in your browser
rem
rem  Stop the server: press Ctrl+C in this window, or close the window.
rem  Options:  run 8001        use another port (default 8000)
rem            set CRASHLAB_NO_BROWSER=1 before running to skip the browser
rem ======================================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title AGENT CRASH LAB

set "PORT=%~1"
if "%PORT%"=="" set "PORT=8000"
set "URL=http://127.0.0.1:%PORT%"
set "VPY=.venv\Scripts\python.exe"

echo.
echo  AGENT CRASH LAB
echo  ---------------
echo.

rem ---- 1. Find Python 3.11+ ---------------------------------------------
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY if not exist "%VPY%" (
  echo  [X] Python 3.11 or newer was not found.
  echo.
  echo      1. Download it from https://www.python.org/downloads/
  echo      2. In the installer, TICK "Add python.exe to PATH"
  echo      3. Then double-click run.bat again.
  echo.
  pause
  exit /b 1
)

rem ---- 2. Create the virtual environment (first run only) ----------------
if not exist "%VPY%" (
  echo  [1/3] Creating the project's private Python environment...
  %PY% -m venv .venv
  if errorlevel 1 goto :fail
) else (
  echo  [1/3] Python environment found.
)

rem ---- 3. Install libraries only when requirements changed ---------------
set "NEED_INSTALL="
if not exist ".venv\deps.lock" set "NEED_INSTALL=1"
if not exist ".venv\deps.toml" set "NEED_INSTALL=1"
if not defined NEED_INSTALL (
  fc /b requirements.lock .venv\deps.lock >nul 2>&1 || set "NEED_INSTALL=1"
  fc /b pyproject.toml .venv\deps.toml >nul 2>&1 || set "NEED_INSTALL=1"
)
if defined NEED_INSTALL (
  echo  [2/3] Installing libraries. The first time this takes a minute or two...
  "%VPY%" -m pip install --disable-pip-version-check -q --upgrade pip
  rem Hide build-tool warnings (not errors) so a first install isn't a wall of text.
  set "PYTHONWARNINGS=ignore"
  "%VPY%" -m pip install --disable-pip-version-check -q -r requirements.lock -e ".[server]"
  if errorlevel 1 goto :fail
  set "PYTHONWARNINGS="
  copy /y requirements.lock .venv\deps.lock >nul
  copy /y pyproject.toml .venv\deps.toml >nul
) else (
  echo  [2/3] Libraries are up to date.
)

rem ---- 4. Start (or reuse) the server and open the browser ---------------
netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul
if not errorlevel 1 (
  echo  [3/3] Something is already running on port %PORT%.
  echo        If it is Crash Lab, it is opening in your browser now.
  echo        Otherwise run with another port, e.g.:  run 8001
  if not defined CRASHLAB_NO_BROWSER start "" "%URL%/"
  echo.
  pause
  exit /b 0
)

echo  [3/3] Starting the server...
echo.
echo        Website     %URL%/
echo        Dashboard   %URL%/app/
echo        API docs    %URL%/docs
echo.
echo        Press Ctrl+C (or close this window) to stop.
echo.

rem Open the browser once the server answers its health check (max ~30 s).
if not defined CRASHLAB_NO_BROWSER (
  start "" /b "%VPY%" -c "import time,urllib.request,webbrowser;exec('for _ in range(60):\n try:\n  urllib.request.urlopen(\'%URL%/health\',timeout=1);webbrowser.open(\'%URL%/app/\');break\n except Exception:\n  time.sleep(0.5)')"
)

".venv\Scripts\crashlab.exe" serve --port %PORT%
if errorlevel 1 goto :fail
exit /b 0

:fail
echo.
echo  [X] Something went wrong - read the red/yellow messages above.
echo      Common fixes are in docs\HOW-TO-RUN.md, section M (Troubleshooting).
echo      A clean reinstall: delete the .venv folder and run this file again.
echo.
pause
exit /b 1
