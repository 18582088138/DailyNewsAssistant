@echo off
title DailyNewsAssistant

rem One-click workbench launcher.
rem
rem ASCII only, on purpose. A .bat is read in the console's OEM code page (936 on a
rem Chinese Windows, 437 elsewhere), so non-ASCII bytes in the script itself can turn
rem into mojibake or, worse, break a command in half. English also means the file name
rem survives machines whose console cannot render CJK.
rem
rem Why call python.exe directly instead of `conda activate`:
rem   `conda activate` in cmd.exe requires `conda init` first, and on a machine that
rem   never ran it the error is "'conda' is not recognized" -- which reads as
rem   "conda is not installed". The conda path is kept below only as a fallback.

cd /d "%~dp0"

set "DNA_ENV=ov_env_py312"
set "DNA_PY=%USERPROFILE%\miniforge3\envs\%DNA_ENV%\python.exe"

if exist "%DNA_PY%" goto :launch

rem Ask conda itself when the env lives somewhere else.
for /f "delims=" %%p in ('conda run -n %DNA_ENV% python -c "import sys;print(sys.executable)" 2^>nul') do set "DNA_PY=%%p"
if exist "%DNA_PY%" goto :launch

echo.
echo   Cannot find the Python interpreter for conda env %DNA_ENV%.
echo   Expected: %USERPROFILE%\miniforge3\envs\%DNA_ENV%\python.exe
echo.
echo   If miniforge is installed elsewhere, edit DNA_PY in this file.
echo.
pause
exit /b 1

:launch
echo   Starting the workbench, your browser will open automatically...
echo   Closing this window stops the server.
echo.
rem `-m frontends.cli.main` rather than `dna`: the console script only exists after
rem `pip install -e .`, while `-m` works with just the interpreter and the source tree.
"%DNA_PY%" -m frontends.cli.main gui
if errorlevel 1 (
  echo.
  echo   Startup failed - the reason is above. Two common ones:
  echo     port 8080 already in use; missing dependencies (run: dna doctor).
  echo.
  pause
)
