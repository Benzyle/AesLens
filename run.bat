@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem ---- locate python ----
set PY=
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
if not defined PY (
  where python >nul 2>nul && set PY=python
)
if not defined PY (
  where py >nul 2>nul && set PY=py
)
if not defined PY (
  echo [ERROR] Python not found on PATH.
  echo         Please install Python 3.9+ and add it to PATH,
  echo         or edit this file and set PY to your interpreter path.
  pause
  exit /b 1
)

rem ---- check inference engine ----
if not exist "tools\llama-vulkan\llama-server.exe" (
  echo [INFO] tools\llama-vulkan\llama-server.exe not found.
  echo [INFO] Downloading llama.cpp prebuilt binaries...
  %PY% scripts\fetch_llama_cpp.py
  if not exist "tools\llama-vulkan\llama-server.exe" (
    echo [ERROR] Inference engine not ready. See docs\llama-cpp.md
    pause
    exit /b 1
  )
)

rem ---- check Pillow ----
%PY% -c "import PIL" >nul 2>nul
if errorlevel 1 (
  echo [INFO] Installing dependencies...
  %PY% -m pip install -r requirements.txt
)

rem ---- check models (any quant variant) ----
%PY% scripts\check_models.py

echo [INFO] Starting the launcher...
start "" %PY% scripts\launcher.py

set RETRY=0
:wait
%PY% -c "import socket,sys;s=socket.socket();s.settimeout(1);sys.exit(0 if s.connect_ex(('127.0.0.1',8081))==0 else 1)" >nul 2>nul
if errorlevel 1 (
  set /a RETRY+=1
  if !RETRY! GEQ 40 goto timeout
  goto wait
)
start "" http://127.0.0.1:8081
echo [OK] Launcher opened: http://127.0.0.1:8081
echo      Closing this window does NOT stop the service. Run stop.bat to stop it.
pause
goto :eof

:timeout
echo [ERROR] Startup timed out. Run stop.bat to free port 8081, then retry.
pause
