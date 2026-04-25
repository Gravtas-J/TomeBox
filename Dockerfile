FROM python:3.11-slim

# Install FFmpeg from apt (more reliable than bundling for Linux)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Create app user (don't run as root)
RUN useradd --system --create-home --shell /bin/bash tomebox

WORKDIR /app

# Install Python dependencies first (better layer caching)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=tomebox:tomebox . .

# Create data directories
RUN mkdir -p /app/data /app/logs /app/covers && \
    chown -R tomebox:tomebox /app/data /app/logs /app/covers

USER tomebox

# Expose the web companion port
EXPOSE 8000

# Persist data, logs, and covers across container restarts
VOLUME ["/app/data", "/app/logs", "/app/covers"]

# Default to headless mode
CMD ["python", "main.py", "--headless", "--host", "0.0.0.0", "--port", "8000"]