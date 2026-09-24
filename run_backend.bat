@echo off
echo Starting FastAPI Backend...
uvicorn main:app --app-dir backend --reload --reload-dir backend --port 8000
pause
