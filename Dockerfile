# ContentStudio AI Backend - Docker Image
# Build: docker build -t contentstudio-backend .
# Run:   docker run -p 8000:8000 --env-file .env contentstudio-backend

FROM python:3.11-slim

LABEL maintainer="ContentStudio Team"
LABEL description="ContentStudio AI Backend - Multi-platform content generation with brand awareness"

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create storage directory for media files
RUN mkdir -p storage/media

# Expose API port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/health', timeout=5)" || exit 1

# Default command - run the API
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
