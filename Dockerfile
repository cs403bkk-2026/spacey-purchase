FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev --no-install-project

COPY app.py ./

ARG APP_REVISION=local
ENV APP_REVISION=$APP_REVISION PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

CMD ["gunicorn", "-b", "0.0.0.0:8000", "app:create_app()"]
