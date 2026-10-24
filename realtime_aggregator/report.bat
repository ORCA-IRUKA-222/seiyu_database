@echo off
setlocal
cd /d "%~dp0"
call :findpy || goto end
%PY% -m yahoo_rt report -c config.json --open
:end
pause
goto :eof

:findpy
where py >nul 2>nul && (set "PY=py -3" & exit /b 0)
where python >nul 2>nul && (set "PY=python" & exit /b 0)
echo Python was not found. Install Python 3.10 or later from https://www.python.org/
echo and check "Add python.exe to PATH" during setup.
exit /b 1
