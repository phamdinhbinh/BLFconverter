@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 goto failed
set "BLF_CHECK_ONLY="
if "%~1"=="--check-only" set "BLF_CHECK_ONLY=1"
if exist ".venv\Scripts\python.exe" goto check_existing
if exist ".venv" goto invalid_venv

rem Use an installed interpreter; do not install Python or change system settings.
set "BLF_PYTHON_COMMAND="
set "BLF_REQUIRED_MINOR=14"
call :probe_python py -3.14
if defined BLF_PYTHON_COMMAND goto create_venv
call :probe_python python
if defined BLF_PYTHON_COMMAND goto create_venv
set "BLF_REQUIRED_MINOR=13"
call :probe_python py -3.13
if defined BLF_PYTHON_COMMAND goto create_venv
call :probe_python python
if defined BLF_PYTHON_COMMAND goto create_venv
set "BLF_REQUIRED_MINOR=12"
call :probe_python py -3.12
if defined BLF_PYTHON_COMMAND goto create_venv
call :probe_python python
if defined BLF_PYTHON_COMMAND goto create_venv
echo No supported Python was found by the Windows launcher or PATH.
echo Use installed CPython 3.12, 3.13 or 3.14, 64-bit, with Tcl/Tk.
echo Free-threaded Python builds are not supported by this launcher.
echo See README_VI.md for VS Code terminal instructions.
goto failed

:create_venv
echo Creating a project-local environment with %BLF_PYTHON_COMMAND%...
%BLF_PYTHON_COMMAND% -m venv ".venv"
if errorlevel 1 goto failed

:check_existing
".venv\Scripts\python.exe" -c "import sys, sysconfig, tkinter; sys.exit(0 if sys.implementation.name == 'cpython' and (3, 12) <= sys.version_info[:2] < (3, 15) and sys.maxsize > 2**32 and not sysconfig.get_config_var('Py_GIL_DISABLED') else 1)" >nul 2>&1
if errorlevel 1 goto invalid_venv

rem A healthy environment with the pinned packages also runs without internet.
".venv\Scripts\python.exe" -c "import sys; from importlib.metadata import version; from pathlib import Path; specs = [line.strip().split('==', 1) for line in Path('requirements.txt').read_text(encoding='utf-8').splitlines() if line.strip() and not line.lstrip().startswith('#')]; (specs and all(len(item) == 2 and version(item[0]) == item[1] for item in specs)) or sys.exit(1); import numpy, cantools, asammdf, tkinter" >nul 2>&1
if not errorlevel 1 goto launch
echo Installing project-local packages. Internet or an approved wheel source is required.
echo Prebuilt wheels only: no C compiler or Visual Studio Build Tools will be invoked.
".venv\Scripts\python.exe" -m pip install --only-binary=:all: -r requirements.txt
if errorlevel 1 goto package_failed
".venv\Scripts\python.exe" -c "import numpy, cantools, asammdf, tkinter"
if errorlevel 1 goto package_failed

:launch
if defined BLF_CHECK_ONLY (
  echo Python and pinned packages are ready.
  exit /b 0
)
".venv\Scripts\python.exe" -m blf_converter
if errorlevel 1 goto failed
exit /b 0

:probe_python
%* -c "import os, sys, sysconfig, tkinter; sys.exit(0 if sys.implementation.name == 'cpython' and sys.version_info[:2] == (3, int(os.environ['BLF_REQUIRED_MINOR'])) and sys.maxsize > 2**32 and not sysconfig.get_config_var('Py_GIL_DISABLED') else 1)" >nul 2>&1
if errorlevel 1 exit /b 0
set "BLF_PYTHON_COMMAND=%*"
exit /b 0

:invalid_venv
echo The existing .venv is incomplete or uses an unsupported Python build.
echo Close this app and any terminals using .venv, then rename .venv to .venv_old.
echo Run this launcher again to create a fresh project-local environment.
echo Nothing has been deleted or replaced by this launcher.
goto failed

:package_failed
echo Package setup or import failed. See the error above.
echo If company network policy blocks package downloads, ask IT for an approved wheel source.
goto failed

:failed
echo Setup or application failed. Check the messages above and README_VI.md.
if /i not "%CI%"=="true" pause
exit /b 1
