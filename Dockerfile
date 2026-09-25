FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Build wheels once in the builder layer so the runtime image stays small.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip setuptools wheel && \
    pip wheel --wheel-dir /wheels -r requirements.txt


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860

WORKDIR /app

# Create an unprivileged runtime user.
RUN groupadd --system app && \
    useradd --system --create-home --gid app --uid 1000 app

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt && \
    rm -rf /wheels

# Copy application code after dependency installation so code-only changes stay cache-friendly.
COPY --chown=app:app . .

# Create runtime directories used by Django.
RUN mkdir -p /app/staticfiles /app/media/uploads /app/Swiggy/Agent && \
    chown -R app:app /app && \
    chmod 755 /app/start.py

USER app

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "from urllib.request import urlopen; urlopen('http://127.0.0.1:7860/healthz/').read()"]

CMD ["python", "/app/start.py"]
