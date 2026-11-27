@echo off
rem Double-click to set Scout up, or to change your answers later.
cd /d "%~dp0"
set PYTHONUTF8=1
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>nul
if errorlevel 1 (
  echo Installing uv, which runs Scout. This happens once.
  powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)
echo Getting Scout ready. The first time downloads Python and a few parts, so give it a few minutes.
uv run --no-dev scout setup
echo.
echo Setup is finished. Double-click start.bat to open Scout.
pause
