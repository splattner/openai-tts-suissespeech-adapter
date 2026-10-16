FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY --from=ghcr.io/astral-sh/uv:0.9.2 /uv /usr/local/bin/uv
RUN uv sync --locked --no-dev --no-install-project
COPY adapter ./adapter
USER 10001:10001
EXPOSE 8000
CMD [".venv/bin/uvicorn", "adapter.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
