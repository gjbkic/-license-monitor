@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 setup_applicant.py --gui
goto done
:use_python
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3 is required. Install it from python.org and try again.
  pause
  exit /b 1
)
python setup_applicant.py --gui
:done
if errorlevel 1 pause
