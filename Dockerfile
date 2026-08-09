FROM python:3.11-slim

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

# Create necessary directories with proper permissions
RUN mkdir -p /app/book_analysis_project/staticfiles && \
    mkdir -p /app/book_analysis_project/jupiter_vectordb_enhanced && \
    chmod -R 777 /app/book_analysis_project/jupiter_vectordb_enhanced

# Make start script executable
RUN chmod +x /app/start.py

EXPOSE 7860

# Run the application
CMD ["python", "/app/start.py"]
