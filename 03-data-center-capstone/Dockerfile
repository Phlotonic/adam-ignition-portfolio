# =============================================================================
# Dockerfile — FastAPI Anomaly Detection Agent
#
# Builds a minimal, production-ready container for the ai_anomaly_agent.py
# FastAPI service.
#
# Base: python:3.11-slim
#   - Debian Bullseye slim variant (~50 MB vs 1.1 GB for full)
#   - No build tools or dev headers (added only when needed)
#   - Official Python 3.11 runtime (matches local dev environment)
#
# Build:  docker build -t anomaly-agent .
# Run:    docker run -p 8000:8000 anomaly-agent
# Compose: docker compose up -d (preferred — uses environment variables)
# =============================================================================

FROM python:3.11-slim

# =============================================================================
# METADATA
# =============================================================================
LABEL maintainer="Adam Dryden <adryden508@gmail.com>"
LABEL description="Data center thermal anomaly detection agent (FastAPI + MQTT + sklearn)"
LABEL version="1.0.0"

# =============================================================================
# SYSTEM DEPENDENCIES
# Install only the minimal OS packages needed:
#   - gcc: required to compile any C extensions in numpy/scipy wheels
#   - libpq-dev: PostgreSQL client headers (needed by asyncpg wheel build)
#
# The `--no-install-recommends` flag prevents apt from pulling in optional
# packages, keeping the image layer small.
# `rm -rf /var/lib/apt/lists/*` removes the apt cache, reducing image size.
# =============================================================================
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# =============================================================================
# WORKING DIRECTORY
# All application files live under /app.
# This is the standard convention for Python microservices in Docker.
# =============================================================================
WORKDIR /app

# =============================================================================
# PYTHON DEPENDENCIES
# Copy requirements first (before application code) to take advantage of
# Docker's layer caching. If only ai_anomaly_agent.py changes, this layer
# (which is slow to build due to scikit-learn compilation) is not rebuilt.
# =============================================================================
COPY requirements.txt .

# Install Python packages
# --no-cache-dir: don't cache pip's download cache (reduces image size)
# --upgrade pip: ensure pip itself is current (avoids deprecation warnings)
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# =============================================================================
# APPLICATION CODE
# Copy the application file(s). In a larger project, this would be:
#   COPY src/ ./src/
# For this single-file service, we copy directly.
# =============================================================================
COPY ai_anomaly_agent.py .

# =============================================================================
# RUNTIME USER
# Create a non-root user for the application.
# Running as root inside a container is a security risk: if the application
# is compromised, an attacker has root-level access to the container filesystem.
# Non-root user prevents writes to system directories.
# =============================================================================
RUN useradd --no-create-home --shell /bin/false appuser
USER appuser

# =============================================================================
# EXPOSED PORTS
# Document that the container listens on port 8000.
# This is informational only — it does not publish the port to the host.
# The actual port mapping is in docker-compose.yml.
# =============================================================================
EXPOSE 8000

# =============================================================================
# HEALTH CHECK
# Docker will call this command periodically to determine container health.
# wget is available in python:3.11-slim; curl is not (use wget instead).
# =============================================================================
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://localhost:8000/health \
    || exit 1

# =============================================================================
# STARTUP COMMAND
# uvicorn: production-grade ASGI server for FastAPI
#   --host 0.0.0.0:  listen on all interfaces (required inside Docker)
#   --port 8000:     matches EXPOSE above
#   --workers 1:     single worker (we use a background MQTT thread; multiple
#                    workers would each start their own MQTT subscription,
#                    causing duplicate anomaly detections — use 1 worker here,
#                    or refactor to use a separate MQTT consumer process)
#   --log-level info: standard operational logging verbosity
# =============================================================================
CMD ["uvicorn", "ai_anomaly_agent:app",
     "--host", "0.0.0.0",
     "--port", "8000",
     "--workers", "1",
     "--log-level", "info"]
