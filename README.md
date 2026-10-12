# spacey-purchase

The Purchase service of Spacey: members and accounts, spaces, and bookings. It is being split out of the [`spacey`](https://github.com/cs403bkk-2026/spacey) monolith, which stays the hub that the frontend talks to; `spacey/purchase/` calls this service. The plan is the issue PUR-000 (#3).

## Quick start

Requires [uv](https://docs.astral.sh/uv/) (Python 3.12 is pinned in `.python-version`).

```bash
uv sync
docker compose up -d --wait db
export DATABASE_URL=postgresql://purchase:purchase@localhost:5435/purchase PURCHASE_TOKEN=dev
uv run flask --app app run --port 8002
curl localhost:8002/health        # {"revision":"local","status":"ok"}
uv run pytest
```

With Docker:

```bash
docker build --build-arg APP_REVISION=$(git rev-parse HEAD) -t spacey-purchase .
docker run --rm -p 8002:8000 --add-host=host.docker.internal:host-gateway \
  -e DATABASE_URL=postgresql://purchase:purchase@host.docker.internal:5435/purchase \
  -e PURCHASE_TOKEN=dev spacey-purchase
```

## Settings

| Variable | |
|---|---|
| `DATABASE_URL` | Required. The Purchase database. |
| `PURCHASE_TOKEN` | Required. The service token the hub sends as `Authorization: Bearer <token>`. |
| `APP_REVISION` | Optional. The revision `/health` reports; `local` when unset. |
| `PURCHASE_TEST_RESET` | Tests and local runs only, never in production. `1` adds `POST /test/reset`, which empties the tables. |
| `SEED_STARTER_SPACE` | Tests and local runs only, never in production. `true` adds the space "Founders Desk" to an empty database. |

A missing required setting or an unreachable database stops the start with a `RuntimeError`.

## Endpoints

- `GET /health`: `200` with `{"status": "ok", "revision": "<APP_REVISION or \"local\">"}`, or `503` with `{"status": "error", "error": "database unreachable"}`. No authentication.
- Any other request without `Authorization: Bearer <PURCHASE_TOKEN>` answers `403` with `{"error": "invalid service token"}`.

## Layout

```
app.py              create_app(): the Flask app and /health
purchase/           the Purchase domain (mirrored by spacey/purchase/)
tests/              tests of the app
openapi.yaml        the HTTP contract
docs/adr/           architecture decisions
docs/deployment.md  how a release is run, checked and rolled back
deploy/             the Nomad job file
spacey_app.py       verbatim copies of spacey files, kept as a reference
tests/spacey_test_app.py   until their content has moved here
```

## Working here

- [CONTRIBUTING.md](CONTRIBUTING.md): branches, commit and PR titles, reviews, local checks.
- [CODING_STANDARDS.md](CODING_STANDARDS.md): the rules reviewers apply.
- [docs/adr/](docs/adr/): why the service is shaped this way.
- [docs/deployment.md](docs/deployment.md): service address, deployed revision, release and the SRE check.
