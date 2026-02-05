# Omega Cloud Dashboard
#
# Dockerfile for deploying the Flask dashboard to Cloud Run
# This enables "access from anywhere" - the core of the cloud migration vision

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY cloud/cloud_dashboard/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY dashboard.py /app/
COPY omega_db.py /app/
COPY config.py /app/
COPY profiles.py /app/
COPY templates/ /app/templates/
COPY notification_manager.py /app/
COPY email_utils.py /app/
COPY job_logs.py /app/
COPY gcs_jobs.py /app/

# Copy workers and providers modules
COPY workers/ /app/workers/
COPY providers/ /app/providers/

# Create necessary directories
RUN mkdir -p /app/heartbeats /app/logs

# Environment defaults
ENV PORT=8080
ENV FLASK_ENV=production
ENV DB_TYPE=postgres

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PORT}/healthz || exit 1

EXPOSE 8080

# Run with gunicorn for production
CMD exec gunicorn --bind :$PORT --workers 2 --threads 4 --timeout 120 dashboard:app
