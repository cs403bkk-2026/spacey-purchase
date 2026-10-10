FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev --no-install-project

COPY app.py gunicorn.conf.py ./

RUN useradd --system --no-create-home app
USER app

ARG APP_REVISION=local
ENV APP_REVISION=$APP_REVISION PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

CMD ["gunicorn", "--config", "gunicorn.conf.py", "--bind", "0.0.0.0:8000", "app:create_app()"]
