# Retail kiosk backend: FastAPI on Uvicorn, for Cloud Run (or any container host).
#
#   docker build -t retail-kiosk-backend .
#   docker run -p 8080:8080 --env-file .env retail-kiosk-backend
#
# No secrets or credentials are baked in: configuration comes from environment
# variables (Cloud Run: --env-vars-file / --set-secrets). See docs/DEPLOYMENT.md.

ARG PYTHON_VERSION=3.14

# ---- build: dependencies in a virtualenv, versions pinned by requirements.lock ----
FROM python:${PYTHON_VERSION}-slim AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt requirements.lock ./
RUN pip install -r requirements.txt -c requirements.lock \
    # Precompiled bytecode: imports are most of the cold-start time.
    && python -m compileall -q /opt/venv

# ---- runtime ----
FROM python:${PYTHON_VERSION}-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    APP_ENV=production \
    PORT=8080
RUN useradd --create-home --uid 10001 app
WORKDIR /srv
COPY --from=build /opt/venv /opt/venv
COPY app ./app
# Only used when BUSINESS_DATA_SOURCE=mock (local testing); production reads Google Sheets.
COPY sample_data ./sample_data
RUN python -m compileall -q app
USER app
EXPOSE 8080

# One worker on purpose: sessions, the data cache and the TTS clip cache live in
# process memory. Scale with more instances only after moving sessions to Redis.
# `sh -c` only to expand $PORT; `exec` makes uvicorn PID 1 so it gets SIGTERM directly
# and shuts down gracefully (Cloud Run allows 10 s).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 75 --no-server-header"]
