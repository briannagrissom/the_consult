FROM python:3.12-slim-bookworm

# Install dependencies including gcloud CLI
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y curl gnupg && \
    echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | tee -a /etc/apt/sources.list.d/google-cloud-sdk.list && \
    curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | apt-key --keyring /usr/share/keyrings/cloud.google.gpg add - && \
    apt-get update && \
    apt-get install -y google-cloud-cli && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir --upgrade pip && \
    pip install uv && \
    useradd -ms /bin/bash app -d /app -u 1000 && \
    mkdir -p /app && \
    chown app:app /app

USER app

WORKDIR /app

COPY --chown=app:app . ./

# Set the env var
ENV UV_LINK_MODE=copy
ENV UV_PROJECT_ENVIRONMENT=/app/.venv

RUN uv sync

# Make docker-shell.sh executable
RUN chmod +x /app/docker-shell.sh

ENTRYPOINT ["/app/docker-shell.sh"]

CMD []
