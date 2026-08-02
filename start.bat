@echo off
echo ============================================
echo   VidSnap - Setup and Start
echo ============================================

echo.
echo [1/2] Installing Python dependencies...
pip install fastapi "uvicorn[standard]" yt-dlp aiofiles python-multipart
if %errorlevel% neq 0 (
    echo ERROR: pip install failed. Make sure Python is installed and in PATH.
    pause
    exit /b 1
)

echo.
echo [2/2] Starting VidSnap server...
echo.
echo  Open your browser at: http://localhost:8000
echo  API docs at:          http://localhost:8000/api/docs
echo  Press Ctrl+C to stop.
echo.

cd /d "%~dp0"
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

pause
