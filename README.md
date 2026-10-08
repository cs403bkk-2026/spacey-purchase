# spacey-purchase

Purchase service, split out of the `spacey` monolith.

## Setup, run and test

Requires [uv](https://docs.astral.sh/uv/) (Python 3.12 is pinned in `.python-version`).

```
uv sync
uv run flask --app app run --port 8001
uv run pytest
```

Port 8001 lets it run next to `spacey` on 8000.

## Endpoints

- `GET /health`: `200` with `{"status": "ok", "revision": "<APP_REVISION or \"local\">"}`. No authentication.
