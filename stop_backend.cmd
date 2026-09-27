@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -X utf8 scripts\start_backend.py --stop
) else (
    uv run --frozen python -X utf8 scripts\start_backend.py --stop
)
echo.
pause
