FROM python:3.12-slim-bookworm

WORKDIR /app

# Install curl for container healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libexpat1 \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies first for layer caching
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

# Bundle the WhiteboxTools binary so the API is ready after startup.
RUN python -c "from whitebox import download_wbt; download_wbt(verbose=False)"

# Create directory structure for data persistence
RUN mkdir -p /data/jobs /data/input /data/output

ENV DEM_DRAINAGE_HOST=0.0.0.0 \
    DEM_DRAINAGE_PORT=8055 \
    DEM_DRAINAGE_JOB_DIR=/data/jobs \
    PYTHONUNBUFFERED=1

EXPOSE 8055

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8055/api/health || exit 1

CMD ["dem-drainage-web"]
