@echo off
setlocal
set "DIR=%~dp0.."
set "PY=%PDFK_PYTHON%"
if "%PY%"=="" set "PY=python"
set "PYTHONPATH=%DIR%\cli;%PYTHONPATH%"
"%PY%" -m pdfk %*
