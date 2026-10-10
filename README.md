# spacey-purchase

The Purchase service of Spacey: members and accounts, spaces, and bookings. It is being split out of the [`spacey`](https://github.com/cs403bkk-2026/spacey) monolith, which stays the hub that the frontend talks to; `spacey/purchase/` calls this service. The plan is the issue PUR-000 (#3).

## Quick start

Requires [uv](https://docs.astral.sh/uv/) (Python 3.12 is pinned in `.python-version`).

```bash
uv sync
uv run flask --app app run --port 8002
curl localhost:8002/health        # {"revision":"local","status":"ok"}
uv run pytest
```

With Docker:

```bash
docker build --build-arg APP_REVISION=$(git rev-parse HEAD) -t spacey-purchase .
docker run --rm -p 8002:8000 spacey-purchase
```

## Endpoints

- `GET /health`: `200` with `{"status": "ok", "revision": "<APP_REVISION or \"local\">"}`. No authentication.

## Layout

```
app.py              create_app(): the Flask app and /health
purchase/           the Purchase domain (mirrored by spacey/purchase/)
tests/              tests of the app
openapi.yaml        the HTTP contract
docs/adr/           architecture decisions
spacey_app.py       verbatim copies of spacey files, kept as a reference
tests/spacey_test_app.py   until their content has moved here
```

## Working here

- [CONTRIBUTING.md](CONTRIBUTING.md): branches, commit and PR titles, reviews, local checks.
- [CODING_STANDARDS.md](CODING_STANDARDS.md): the rules reviewers apply.
- [docs/adr/](docs/adr/): why the service is shaped this way.
