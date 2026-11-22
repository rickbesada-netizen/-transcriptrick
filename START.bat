@echo off
setlocal EnableDelayedExpansion
title Transcriptrick
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto ready

echo.
echo   First run: setting up Transcriptrick. This takes a few minutes and only happens once.
echo.
set "TRIED="

:find
set "PY="
for %%V in (3.13 3.14 3.12 3.11) do if not defined PY py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
if defined PY goto make
python -c "import sys; raise SystemExit(0 if (3,11)<=sys.version_info[:2]<=(3,14) else 1)" >nul 2>&1 && set "PY=python"
if defined PY goto make
for %%V in (313 314 312 311) do if not defined PY if exist "%LocalAppData%\Programs\Python\Python%%V\python.exe" set PY="%LocalAppData%\Programs\Python\Python%%V\python.exe"
if defined PY goto make
for %%V in (313 314 312 311) do if not defined PY if exist "%ProgramFiles%\Python%%V\python.exe" set PY="%ProgramFiles%\Python%%V\python.exe"
if defined PY goto make

echo   Transcriptrick needs Python 3.11, 3.12, 3.13 or 3.14, and none of those was found.
echo   This is what Windows has now:
python --version 2>nul
py --list 2>nul
echo.
if defined TRIED goto nopython
choice /c YN /m "  Install Python 3.13 for your Windows user now"
if errorlevel 2 goto nopython
set "TRIED=1"
winget install --exact --id Python.Python.3.13 --scope user --silent --accept-package-agreements --accept-source-agreements
goto find

:nopython
echo.
echo   Install Python 3.13 from https://www.python.org/downloads/windows/
echo   Tick "Add python.exe to PATH" in the installer, then run START.bat again.
echo.
pause
exit /b 1

:make
echo   Using Python: %PY%
%PY% -m venv .venv
if errorlevel 1 (
  echo   The Python environment couldn't be created.
  pause
  exit /b 1
)

:ready
fc /b requirements.txt .venv\installed.txt >nul 2>&1
if errorlevel 1 (
  echo   Installing components...
  ".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
  if errorlevel 1 (
    echo   Installing failed. Check your internet connection and run START.bat again.
    pause
    exit /b 1
  )
  copy /y requirements.txt .venv\installed.txt >nul
) else (
  echo   Checking for downloader updates...
  ".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check --upgrade yt-dlp yt-dlp-ejs "youtube-transcript-api<2" >nul 2>&1
)

".venv\Scripts\python.exe" -m app
if errorlevel 1 pause
