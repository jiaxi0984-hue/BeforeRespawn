@echo off
setlocal
set PYTHONUTF8=1
chcp 65001 >nul
cd /d "%~dp0"
if errorlevel 1 exit /b 1
set "LOL_ANKI_RUNTIME="
if exist "%~dp0runtime-python.txt" set /p "LOL_ANKI_RUNTIME="<"%~dp0runtime-python.txt"
if defined LOL_ANKI_RUNTIME if exist "%LOL_ANKI_RUNTIME%" (
    "%LOL_ANKI_RUNTIME%" -c "import sys, tkinter; assert sys.version_info[:2] == (3, 14)" >nul 2>nul
    if not errorlevel 1 (
        "%LOL_ANKI_RUNTIME%" app.py %*
        if errorlevel 1 pause
        exit /b
    )
)
py -3.14 -c "import sys, tkinter; assert sys.version_info[:2] == (3, 14)" >nul 2>nul
if not errorlevel 1 (
    py -3.14 app.py %*
    if errorlevel 1 pause
    exit /b
)
python -c "import sys, tkinter; assert sys.version_info[:2] == (3, 14)" >nul 2>nul
if not errorlevel 1 (
    python app.py %*
    if errorlevel 1 pause
    exit /b
)
echo Python 3.14 with tkinter is required. See README.md.
pause
