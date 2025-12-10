@echo off
echo Starting FastAPI Backend...
uvicorn backend.main:app --reload --port 8000
pause
