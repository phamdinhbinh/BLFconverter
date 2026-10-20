@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto ready
py -3.12 -c "import sys; assert sys.maxsize > 2**32" >nul 2>&1
if errorlevel 1 (
  echo Please install Python 3.12 64-bit from https://www.python.org/downloads/windows/
  echo Include Tcl/Tk and the Python launcher during installation.
  pause
  exit /b 1
)
py -3.12 -m venv .venv
if errorlevel 1 goto failed
echo Installing packages. Internet is only needed for this first setup.
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
:ready
".venv\Scripts\python.exe" -c "import numpy, cantools, asammdf, tkinter" >nul 2>&1
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m blf_converter
if errorlevel 1 goto failed
exit /b 0
:failed
echo Setup or application failed. Check the error above.
pause
exit /b 1
