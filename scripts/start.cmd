@echo off
setlocal
set "ROOT=%~dp0.."
set "PD_PY=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PD_PY%" set "PD_PY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe"
if not exist "%PD_PY%" set "PD_PY=python"
"%PD_PY%" "%ROOT%\launch.py" %*
if errorlevel 1 pause
