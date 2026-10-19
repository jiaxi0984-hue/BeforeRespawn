@echo off
setlocal
set PYTHONUTF8=1
chcp 65001 >nul
cd /d "%~dp0"
if errorlevel 1 exit /b 1
py -3.14 -c "import sys; assert sys.version_info[:2] == (3,14)" >nul 2>nul
if not errorlevel 1 goto use_launcher
python -c "import sys; assert sys.version_info[:2] == (3,14)" >nul 2>nul
if not errorlevel 1 goto use_python
echo 64-bit Python 3.14 is required. Install it from python.org, then retry.
pause
exit /b 1
:use_launcher
py -3.14 -m pip install --upgrade --only-binary=:all: --target _deps -r requirements.txt
if errorlevel 1 goto failed
py -3.14 -c "import sys; from pathlib import Path; Path('runtime-python.txt').write_text(sys.executable + '\n', encoding='utf-8')"
goto done
:use_python
python -m pip install --upgrade --only-binary=:all: --target _deps -r requirements.txt
if errorlevel 1 goto failed
python -c "import sys; from pathlib import Path; Path('runtime-python.txt').write_text(sys.executable + '\n', encoding='utf-8')"
goto done
:failed
echo Dependency installation failed. Check the connection and retry.
pause
exit /b 1
:done
if errorlevel 1 goto failed
echo Installation completed. Run start.cmd to open the application.
pause
