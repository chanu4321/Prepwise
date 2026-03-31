# ---- Stage 1: Builder ----
# Install Python dependencies in an isolated build stage
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies for psycopg2 etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ---- Stage 2: Final Runtime Image ----
FROM python:3.11-slim

# Install runtime system dependencies (Tesseract OCR, Poppler)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy installed Python packages from the builder stage
COPY --from=builder /install /usr/local

# Copy the backend source into /app/backend so that
# `from backend.services.xxx import ...` imports work correctly
COPY . /app/backend/

# Tell Python to find the `backend` package at /app
ENV PYTHONPATH=/app

# Expose the port uvicorn will listen on
EXPOSE 8000

# CMD uses $PORT env var (Railway/Render compatible) with fallback to 8000
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
