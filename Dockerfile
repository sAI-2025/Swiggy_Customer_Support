FROM python:3.12-slim


ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860

# Install dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install requirements
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create runtime directories used by Django
RUN mkdir -p /app/staticfiles /app/media/uploads /app/Swiggy/Agent && \
    chmod -R 777 /app/media /app/staticfiles /app/Swiggy/Agent

# Make start script executable
RUN chmod +x /app/start.py

EXPOSE 7860

# Run the application
CMD ["python", "/app/start.py"]
