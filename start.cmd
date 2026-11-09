@echo off
cd /d "%~dp0"
python -m bunsen
if errorlevel 1 pause
