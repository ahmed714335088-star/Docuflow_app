@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python 3.11+ is required. Install Python from https://www.python.org/downloads/ and enable PATH.
  pause
  exit /b 1
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo FFmpeg is required and must be on PATH. Install a full FFmpeg build with libass subtitles support.
  echo See https://ffmpeg.org/download.html
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto :failed
)
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :failed
set HOST=127.0.0.1
if "%PORT%"=="" set PORT=3000
echo DocuFlow is ready at http://127.0.0.1:%PORT%
.venv\Scripts\python.exe -m app.main
pause
exit /b 0
:failed
echo Setup failed. Check Python, FFmpeg, and your internet connection.
pause
exit /b 1
