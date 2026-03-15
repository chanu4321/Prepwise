FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    poppler-utils \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the backend folder contents into /app/backend
# This ensures that imports like `from backend.services...` work correctly
COPY . /app/backend/

# Let Python know where the root of the project is
ENV PYTHONPATH=/app

# Railway automatically passes $PORT, we'll configure Uvicorn to listen on it
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
