@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [INFO] Stopping the launcher and the inference service...

rem 1) graceful shutdown of the launcher (if its HTTP API still answers)
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'http://127.0.0.1:8081/api/shutdown' -Method POST -TimeoutSec 3 -ErrorAction SilentlyContinue | Out-Null } catch {}"
timeout /t 1 /nobreak >nul

rem 2) whatever still listens on 8081
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort 8081 -State Listen -ErrorAction SilentlyContinue; if($c){ Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue; Write-Output ('[OK] stopped 8081 -> PID ' + $c.OwningProcess) } else { Write-Output '[OK] 8081 not in use' }"

rem 3) orphan launcher.py processes
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*launcher.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

rem 4) running analysis tasks
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*run_analysis.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

rem 5) the inference server itself
taskkill /F /IM llama-server.exe >nul 2>&1

echo [OK] Done. The launcher (8081) and llama-server (8080) have been stopped.
pause
