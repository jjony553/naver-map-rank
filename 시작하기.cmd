@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" launcher.py %*
  goto done
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 launcher.py %*
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo Python 3.10 or later is required.
    echo Install Python from https://www.python.org/downloads/
    echo Select "Add Python to PATH" during installation.
    pause
    exit /b 1
  )
  python launcher.py %*
)
:done
if errorlevel 1 pause
endlocal
