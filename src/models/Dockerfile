# Use the official Debian-hosted Python image
FROM python:3.12-slim-bookworm

ARG DEBIAN_PACKAGES="build-essential curl libpq5"
# libpq-dev

# Prevent apt from showing prompts
ENV DEBIAN_FRONTEND=noninteractive

# Python wants UTF-8 locale
ENV LANG=C.UTF-8

# Tell Python to disable buffering so we don't lose any logs.
ENV PYTHONUNBUFFERED=1

# Tell uv to copy packages from the wheel into the site-packages
ENV UV_LINK_MODE=copy
ENV UV_PROJECT_ENVIRONMENT=/.venv
# Defaults for running ChromaDB server
ENV CHROMA_DB_PATH=/chroma/chroma
ENV CHROMA_SERVER_HOST=0.0.0.0
ENV CHROMA_SERVER_PORT=8000

# Ensure that user-level installs of Python packages are found
ENV PATH="/home/app/.local/bin:${PATH}"

# Ensure we have an up to date baseline, install dependencies and
# create a user so we don't run the app as root
RUN set -ex; \
    for i in $(seq 1 8); do mkdir -p "/usr/share/man/man${i}"; done && \
    apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends $DEBIAN_PACKAGES && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir --upgrade pip && \
    pip install uv && \
    # useradd -ms /bin/bash app -d /home/app -u 1000 && \
    # since useradd fails if the user already exists, we use this workaround
    id app 2>/dev/null || useradd -ms /bin/bash app -d /home/app -u 1000 && \
    mkdir -p /app && \
    chown app:app /app && \
    mkdir -p /.venv && \
    chown app:app /.venv && \
    mkdir -p "$CHROMA_DB_PATH" && \
    chown -R app:app /chroma

# Switch to the new user
USER app
# WORKDIR /app
WORKDIR /app/src

# Copy dependency files first for better layer caching
COPY --chown=app:app pyproject.toml uv.lock* ./

# Install dependencies in a separate layer for better caching
# RUN uv sync --frozen

# ENV PATH="/home/app/.local/bin:${PATH}"
# Install uv for the app user
# RUN pip install --no-cache-dir uv && \
#     uv sync --frozen

RUN pip install --user uv && \
    export PATH="$PATH:/home/app/.local/bin" && \
    uv sync --frozen

EXPOSE 8000


# Copy the rest of the source code to the app/src directory
COPY --chown=app:app . ./


# Copy entrypoint and ensure it's executable
# COPY --chown=app:app docker-entrypoint.sh /app/docker-entrypoint.sh
# RUN chmod +x /app/docker-entrypoint.sh

# # Entry point
# # ENTRYPOINT ["/bin/bash","docker-entrypoint.sh"]
# ENTRYPOINT ["/bin/bash", "/app/docker-entrypoint.sh"]

COPY --chown=app:app docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh
ENTRYPOINT ["/bin/bash", "/app/docker-entrypoint.sh"]
