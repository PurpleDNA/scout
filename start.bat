@echo off
rem Double-click to open Scout. Once a day it fetches new jobs first.
cd /d "%~dp0"
set PYTHONUTF8=1
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>nul
if errorlevel 1 (
  echo Scout isn't set up yet. Double-click setup.bat first.
  pause
  exit /b 1
)
uv run --no-dev scout start
pause
