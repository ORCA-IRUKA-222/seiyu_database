@echo off
rem Stop collect.bat first. Re-reads all posts since "since" to update like counts, then makes the report.
setlocal
cd /d "%~dp0"
call :findpy || goto end
echo Updating like counts and filling gaps. This can take several minutes.
%PY% -m yahoo_rt collect -c config.json --refresh --live-report
:end
pause
goto :eof

:findpy
where py >nul 2>nul && (set "PY=py -3" & exit /b 0)
where python >nul 2>nul && (set "PY=python" & exit /b 0)
echo Python was not found. Install Python 3.10 or later from https://www.python.org/
echo and check "Add python.exe to PATH" during setup.
exit /b 1
