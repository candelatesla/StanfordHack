#!/usr/bin/env bash
# One-port deploy (Replit): build the UI, FastAPI serves it alongside the API/WebSockets.
set -e
(cd frontend && npm install --no-audit --no-fund && npm run build)
pip install -q -r backend/requirements.txt
python tools/get_voice_model.py
cd backend && exec python -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
