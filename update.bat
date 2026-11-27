@echo off
rem Double-click to get the newest version of Scout. Your answers and saved jobs are kept.
cd /d "%~dp0"
git pull
if errorlevel 1 (
  echo.
  echo The update didn't work. Send a photo of this window to whoever set Scout up for you.
)
pause
