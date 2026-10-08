# spacey-purchase

Purchase service, split out of the `spacey` monolith.

## Setup, run and test

Requires [uv](https://docs.astral.sh/uv/) (Python 3.12 is pinned in `.python-version`).

```
uv sync
uv run flask --app app run --port 8001
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Port 8001 lets it run next to `spacey` on 8000.

## Endpoints

- `GET /health`: `200` with `{"status": "ok", "revision": "<APP_REVISION or \"local\">"}`. No authentication.

## Shared database

Until PUR-008, this service and `spacey` share one database.

## Branch protection

`main` is protected by the ruleset in `.github/rulesets/protect-main.json`: changes go through a pull request with 1 approval from someone other than the last pusher, new pushes dismiss approvals, review threads must be resolved, and `main` cannot be deleted. Org admins can bypass.

Applying it needs repo admin:

```
gh api -X POST repos/cs403bkk-2026/spacey-purchase/rulesets --input .github/rulesets/protect-main.json
gh api -X PATCH repos/cs403bkk-2026/spacey-purchase -F delete_branch_on_merge=true
```

To change an existing ruleset, use `gh api -X PUT repos/cs403bkk-2026/spacey-purchase/rulesets/<id> --input ...` (list ids with `gh api repos/cs403bkk-2026/spacey-purchase/rulesets`).
