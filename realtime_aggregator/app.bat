@echo off
rem Opens the search window. Double-click this file.
setlocal
cd /d "%~dp0"
call :findpy || goto fail
%PY% -c "import tkinter" 2>nul || (echo tkinter was not found. Reinstall Python from https://www.python.org/ with "tcl/tk and IDLE" checked. & goto fail)
rem Use pythonw.exe next to python.exe so that no console window stays open.
set "PYW="
for /f "usebackq delims=" %%i in (`%PY% -c "import os, sys; p = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'); print(p if os.path.isfile(p) else '')"`) do set "PYW=%%i"
if not defined PYW goto console
start "" "%PYW%" -m yahoo_rt gui
exit /b 0

:console
rem pythonw.exe was not found, so run with this window open.
%PY% -m yahoo_rt gui
if errorlevel 1 goto fail
exit /b 0

:fail
pause
exit /b 1

:findpy
where py >nul 2>nul && (set "PY=py -3" & exit /b 0)
where python >nul 2>nul && (set "PY=python" & exit /b 0)
echo Python was not found. Install Python 3.10 or later from https://www.python.org/
echo and check "Add python.exe to PATH" during setup.
exit /b 1
