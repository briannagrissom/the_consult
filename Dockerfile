# Dockerfile Draft (from /models)
# Use the official Debian-hosted Python image
FROM python:3.12-slim-bookworm

# Not sure
ARG DEBIAN_PACKAGES="build-essential curl libpq5"
# libpq-dev

# Prevent apt from showing prompts
ENV DEBIAN_FRONTEND=noninteractive

# Python configuration
ENV LANG=C.UTF-8
ENV PYTHONUNBUFFERED=1

# Tell uv to copy packages from the wheel into the site-packages
ENV UV_PROJECT_ENVIRONMENT=/app/.venv
ENV UV_LINK_MODE=copy
ENV UV_SYSTEM_PYTHON=1

# Ensure that user-level installs of Python packages are found
ENV PATH="/home/app/.local/bin:${PATH}"
ENV PYTHONPATH=/app/src

# Install system dependencies
# Create non-root user
RUN set -ex; \
    # for i in $(seq 1 8); do mkdir -p "/usr/share/man/man${i}"; done && \
    apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends $DEBIAN_PACKAGES && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip uv && \
    # useradd -ms /bin/bash app -d /home/app -u 1000 && \
    # since useradd fails if the user already exists, we use this workaround
    id app 2>/dev/null || useradd -ms /bin/bash app -d /home/app -u 1000 && \
    mkdir -p /app && \
    chown app:app /app && \
    mkdir -p /app/.venv && \
    chown app:app /app/.venv


# Switch to the new user
USER app
WORKDIR /app

# Copy dependency files in pyproject.toml for better layer caching
COPY --chown=app:app pyproject.toml ./
COPY --chown=app:app uv.lock* ./
# Can also add uv.lock* before ./


# Install Python dependencies
# Install uv for the app user
RUN uv sync --frozen


# Copy application code
COPY --chown=app:app src/ ./src/
COPY --chown=app:app tests/ ./tests/
COPY --chown=app:app pytest.ini ./
COPY --chown=app:app docker-entrypoint.sh /app/docker-entrypoint.sh


# Make entry point executable
USER root
RUN chmod +x /app/docker-entrypoint.sh
USER app

# # ENTRYPOINT ["/bin/bash","docker-entrypoint.sh"]
# ENTRYPOINT ["/bin/bash", "/app/docker-entrypoint.sh"]

# Expose port
EXPOSE 8081

# Entry point
ENTRYPOINT ["/bin/bash", "/app/docker-entrypoint.sh"]
# Old: CMD ["uvicorn","api.server:app","--host", "0.0.0.0","--port", "8081"]
