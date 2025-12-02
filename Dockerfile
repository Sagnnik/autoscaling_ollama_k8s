FROM python:3.12-slim-bookworm AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY uv.lock pyproject.toml ./

RUN uv venv && . /app/.venv/bin/activate && uv sync --frozen

FROM python:3.12-slim-bookworm AS final

COPY --from=builder /bin/uv /usr/bin/
COPY --from=builder /app/.venv /app/.venv/

WORKDIR /app

COPY . .

CMD ["uv", "run", "streamlit", "run", "main.py", "--server.port=8501", "--server.address=0.0.0.0"]