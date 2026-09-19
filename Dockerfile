FROM python:3.12-slim-bookworm

WORKDIR /app

# Install curl for container healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies first for layer caching
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

# Copy application source code
COPY src/ ./src/

# Reinstall package in editable mode with full source
RUN pip install --no-cache-dir -e .

# Create directory structure for data persistence
RUN mkdir -p /data/jobs /data/input /data/output

ENV DEM_DRAINAGE_HOST=0.0.0.0 \
    DEM_DRAINAGE_PORT=8000 \
    DEM_DRAINAGE_JOB_DIR=/data/jobs \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["dem-drainage-web"]
