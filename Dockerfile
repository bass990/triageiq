# TriageIQ: one image serves the API (port 8001) and the built React UI.
#
#   docker build -t triageiq .
#   docker run -p 8001:8001 -e ANTHROPIC_API_KEY=sk-ant-... triageiq   # live pipeline on the mock patients
#   docker run -p 8001:8001 -e TRIAGEIQ_DEMO=1 triageiq                # replay the recorded run, no key needed

FROM node:20-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
ENV VITE_API_URL=""
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY config.py ./
COPY backend/ backend/
COPY demo/ demo/
COPY --from=ui /ui/dist frontend/dist
RUN useradd -m app && mkdir -p logs && chown -R app:app /app
USER app
EXPOSE 8001
ENV TRIAGEIQ_CORS_ORIGINS="*" PORT=8001
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health').status==200 else 1)"
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8001"]
