# --- Stage 1: Build dependencies ---
FROM python:3.12-slim AS builder

WORKDIR /build

# Copy only pyproject.toml first for layer caching
COPY pyproject.toml .

# Create a minimal package so pip can resolve the project
RUN mkdir -p claude_mobile && \
    echo '' > claude_mobile/__init__.py

# Install dependencies into a clean prefix
RUN pip install --no-cache-dir --prefix=/install .

# Copy the full application code and reinstall (picks up the actual package)
COPY claude_mobile/ claude_mobile/
RUN pip install --no-cache-dir --prefix=/install .

# --- Stage 2: Runtime ---
FROM python:3.12-slim

# Install runtime dependencies:
#   curl  - healthcheck
#   bash  - default PTY shell
#   git   - git worktree operations and claude sessions
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl bash git && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN addgroup --system app && \
    adduser --system --ingroup app app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Set up application directory
WORKDIR /app
RUN mkdir -p /app/data && chown app:app /app/data

# Environment defaults
ENV HOST=0.0.0.0 \
    PORT=8000 \
    USER_STORE_PATH=/app/data/users.json \
    PROJECTS_STORE_PATH=/app/data/projects.json \
    AGENT_STATE_DIR=/app/data/state \
    LOG_LEVEL=INFO

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD curl -f http://localhost:8000/api/health || exit 1

# NOTE: To run Claude CLI sessions inside the container, Node.js and the
# Claude CLI must also be installed. This is out of scope for the base image.

USER app

ENTRYPOINT ["python", "-m", "claude_mobile"]
