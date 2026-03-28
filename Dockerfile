FROM python:3.11-slim

# System deps for pymupdf (PDF parsing) + C++ build tools for chroma-hnswlib
RUN echo ">>> Installing system dependencies..." && \
    apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgl1 \
    build-essential \
    g++ \
    cmake \
    && rm -rf /var/lib/apt/lists/* && \
    echo ">>> System dependencies installed."

WORKDIR /app

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN echo ">>> Installing Python dependencies (this may take a while)..." && \
    pip install --no-cache-dir -r requirements.txt && \
    echo ">>> Python dependencies installed."

# Copy application code
COPY . .

# Create required directories (will be volume-mounted in production)
RUN mkdir -p profiles system logs static && \
    echo ">>> Build complete. Starting application..."

# HuggingFace Spaces uses port 7860
EXPOSE 7860

# Run with uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
