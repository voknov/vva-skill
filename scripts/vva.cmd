@echo off
setlocal

where py >nul 2>&1
if not errorlevel 1 goto use_py

where python3 >nul 2>&1
if not errorlevel 1 goto use_python3

where python >nul 2>&1
if not errorlevel 1 goto use_python

echo VVA requires Python 3, but py, python3, and python were not found. 1>&2
exit /b 127

:use_py
py -3 "%~dp0vva_client.py" %*
exit /b %errorlevel%

:use_python3
python3 "%~dp0vva_client.py" %*
exit /b %errorlevel%

:use_python
python "%~dp0vva_client.py" %*
exit /b %errorlevel%
