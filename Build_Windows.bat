@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run Run_Windows.bat once to install the runtime first, then close the app.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install "pyinstaller>=6.15,<7"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onedir --windowed --name BLFConverter --collect-all asammdf --collect-all cantools --hidden-import numpy --hidden-import lxml.etree --hidden-import bitstruct.c --collect-all canmatrix --exclude-module PySide6 --exclude-module PyQt6 --exclude-module PyQt5 desktop_entry.py
if errorlevel 1 goto failed
echo Output: dist\BLFConverter\BLFConverter.exe
echo Copy the whole BLFConverter folder to the destination Windows computer.
pause
exit /b 0
:failed
echo Build failed. Check the error above.
pause
exit /b 1
