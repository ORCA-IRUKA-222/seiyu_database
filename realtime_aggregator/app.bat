@echo off
rem Opens the search window. Double-click this file.
setlocal
cd /d "%~dp0"
call :findpy || goto fail
%PY% -c "import tkinter" 2>/dev/null || (echo tkinter was not found. Reinstall Python from https://www.python.org/ with "tcl/tk and IDLE" checked. & goto fail)
start "" %PYW% -m yahoo_rt gui
exit /b 0

:fail
pause
exit /b 1

:findpy
where pyw >/dev/null 2>/dev/null && (set "PY=py -3" & set "PYW=pyw -3" & exit /b 0)
where pythonw >/dev/null 2>/dev/null && (set "PY=python" & set "PYW=pythonw" & exit /b 0)
echo Python was not found. Install Python 3.10 or later from https://www.python.org/
echo and check "Add python.exe to PATH" during setup.
exit /b 1
