FROM python:3.12-slim
RUN apt-get update && apt-get install -y curl git && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY entrypoint.sh /opt/cardea/entrypoint.sh
RUN chmod +x /opt/cardea/entrypoint.sh
ENTRYPOINT ["/opt/cardea/entrypoint.sh"]
