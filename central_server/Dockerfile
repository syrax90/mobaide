# --- Stage 1: Build dependencies ---
FROM python:3.12-slim AS builder

WORKDIR /build

# Copy only pyproject.toml first for layer caching
COPY pyproject.toml .

# Create a minimal package so pip can resolve the project
RUN mkdir -p central_server && \
    echo '' > central_server/__init__.py

# Install dependencies into a clean prefix
RUN pip install --no-cache-dir --prefix=/install .

# Copy the full application code and reinstall (picks up the actual package)
COPY central_server/ central_server/
RUN pip install --no-cache-dir --prefix=/install .

# --- Stage 2: Runtime ---
FROM python:3.12-slim

# Install runtime dependencies (curl for healthcheck only — no PTY, git, or bash needed)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN addgroup --system app && \
    adduser --system --ingroup app app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Set up application directory
WORKDIR /app
RUN mkdir -p /app/data && chown app:app /app/data

# Copy templates and static files
COPY templates/ templates/
COPY static/ static/

# Environment defaults
ENV HOST=0.0.0.0 \
    PORT=9000 \
    DB_PATH=/app/data/central_server.db \
    LOG_LEVEL=INFO

EXPOSE 9000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD curl -f http://localhost:9000/api/health || exit 1

USER app

ENTRYPOINT ["python", "-m", "central_server"]
