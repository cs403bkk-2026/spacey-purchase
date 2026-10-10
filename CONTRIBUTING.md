# Contributing to spacey-purchase

How the Purchase team works in this repository. Read it once before your first PR; the checks in CI enforce most of it.

## The flow in one picture

```
issue ─▶ branch off main ─▶ commits ─▶ pull request ─▶ checks green + 1 approval ─▶ squash-merge into main
```

1. **Pick an issue.** The plan is the parent issue PUR-000 and its sub-issues. Assign yourself before you start.
2. **Branch off an up-to-date `main`** (see [Branches](#branches)).
3. **Commit as often as you like** on your branch. Only the PR title reaches `main`.
4. **Open a pull request** early; mark it *Draft* until it is ready for review.
5. **Merge** with *Squash and merge* once the checks are green and one teammate has approved. GitHub deletes the branch.

## Branches

We use **gitflow with `main` as the development branch**:

- `main` is the only long-lived branch. It must always pass CI and be safe to release. There is **no `develop` and no `release/*` branch**.
- All work happens on **short-lived branches** cut from `main` and merged back by a PR. Never push to `main` directly.
- Keep a branch to one PR and a few days. Pull `main` into it (or press *Update branch*) when it falls behind.

**Name:** `<type>/<short-name>`, with the ticket id first when there is one.

| Prefix | For | Example |
|---|---|---|
| `feature/` | new behaviour | `feature/pur-003-run-once-migrations` |
| `fix/` | a bug fix | `fix/pur-004-pay-once` |
| `refactor/` | a change with no behaviour change | `refactor/pur-012-metrics-module` |
| `test/` | tests only | `test/pur-004-payment-outcomes` |
| `docs/` | documentation only | `docs/contributing` |
| `ci/`, `build/`, `chore/`, `perf/`, `revert/` | CI, image and dependencies, upkeep, speed, undoing a merge | `chore/update-flask` |

Lower case, digits, `-`, `.` and `_` only. The `pr-title` check rejects other names. GitHub's *Create a branch* button on an issue makes names like `12-pur-009-…`; rename them before opening the PR (`git branch -m feature/pur-009-…`).

## Commit messages and PR titles

We follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/). Because every PR is squash-merged, **the PR title becomes the commit on `main`**, so the PR title is the one that must be right:

```
<type>(<scope>): <what it does, imperative, lower case> (PUR-NNN)
```

| Part | Rule |
|---|---|
| `type` | `feat` (new behaviour), `fix` (bug fix), `refactor`, `perf`, `test`, `docs`, `build` (image, dependencies), `ci`, `chore`, `revert` |
| `scope` | Optional. `purchase` for service code; or the area: `ci`, `docker`, `db`, `api` |
| description | Imperative, lower case, no full stop: "add …", "fix …", "remove …" |
| `(PUR-NNN)` | The ticket id, when the PR belongs to one |

Examples:

```
feat(purchase): create the Purchase database through run-once migrations (PUR-003)
fix(purchase): record a payment only once (PUR-004)
refactor(purchase): move compute_metrics into purchase/metrics.py (PUR-012)
ci: run the tests against Postgres
```

**Breaking change** to our public API (a path, method, status or body that a caller relies on): add `!` before the colon and a `BREAKING CHANGE:` line in the PR body. For example, `feat(purchase)!: answer 410 on subscribe (PUR-001)`.

Commits inside your branch are yours to name. Following the same format helps reviewers but is not checked.

## Pull requests

- **One PR per checklist item group** in the issue's *What to do*. Small PRs get reviewed faster.
- **Fill in the template**: what and why, how you checked it (commands and their output), and what you did not check.
- **Link the issue**: `Refs #N` on a ticket's earlier PRs, `Closes #N` on its last PR.
- **Before asking for review**, run the [local checks](#local-checks). CI runs the same ones.

**Required to merge** (the ruleset enforces these):

| Check | What it means |
|---|---|
| `pr-title` | Title and branch name follow the rules above |
| `lint` | `uv lock --check`, `ruff check`, `ruff format --check` pass |
| `test` | `pytest` passes |
| `secrets` | gitleaks finds no secret anywhere in the history |
| `docker` | The image builds, and its `/health` reports this commit |
| 1 approval | From a teammate who did not push the last commit; every conversation resolved |
| Code owners | The Purchase team approves every change; the architects also approve changes to `openapi.yaml` and `docs/adr/` |
| Up to date | The branch contains the latest `main` (press *Update branch*) |

**Merging:** the author merges with **Squash and merge** and keeps the PR title as the commit title. Merge commits and rebase merges are switched off.

### Reviewing

- Review against [CODING_STANDARDS.md](CODING_STANDARDS.md), the issue's *Done when*, and the contract in `openapi.yaml`.
- Prefer GitHub *suggestions* for small fixes. Say whether a comment blocks the merge.
- Approve only what you would be happy to maintain yourself.

## Local checks

You need [uv](https://docs.astral.sh/uv/) and Docker. Python 3.12 is pinned in `.python-version`.

```bash
uv sync                          # install everything, including dev tools
uv run ruff check .              # lint
uv run ruff format --check .     # formatting (drop --check to fix it)
uv lock --check                  # uv.lock matches pyproject.toml
uv run pytest                    # tests
```

Run the service and check `/health`:

```bash
uv run flask --app app run --port 8002
curl localhost:8002/health       # {"revision":"local","status":"ok"}
```

Optional: `uvx pre-commit install` runs the lint and format checks on every commit.

Add a dependency with `uv add <package>==<version>` (or `uv add --dev …`), and commit both `pyproject.toml` and `uv.lock`. There is no `requirements.txt`.

**Local ports.** `spacey` uses 8000, Payments 8001, Access 8003. Purchase uses **8002**.

## Tests

- Every new rule, default or error branch gets a test in the same PR ([CODING_STANDARDS.md](CODING_STANDARDS.md#tests-ch-9)).
- Tests never call another team's service. When a flow depends on another service's answer, the test feeds each possible answer into our endpoint instead (for example, a payment that succeeded, failed, is unknown or was refunded).

## Working with the other repositories

The system is five repositories. We own this one; `spacey` is shared; the rest we only read.

| Repository | Our role |
|---|---|
| `spacey-purchase` (this one) | We own it: code, settings, CI, rules |
| `spacey` | The hub that serves production. We maintain its `purchase/` folder and the shared lines our tickets name (for example in `app.py`) |
| `spacey-frontend`, `spacey-payments`, `spacey-access` | Read-only for us. We use their APIs, never their code |

- **`spacey/purchase/` mirrors this repo's `purchase/`.** The same module names and function names, so `spacey/purchase/booking.py` calls the code in our `purchase/booking.py`. When a ticket changes our module layout, its PR in `spacey` changes the mirror in the same way; each PR links the other.
- In `spacey`, change only `purchase/` and the shared lines your ticket names, and follow that repo's own contributing rules.
- **Other teams are reached through their APIs and our contract**, not through comments on their issues. Our PRs and `openapi.yaml` are the signal. A business-rule change is a PR to `spacey-business-rules`.
- When you mention another team's issue or PR, write it as a code span (`` `spacey-access#20` ``), so GitHub does not post a backlink on their page.

## Secrets and data

This repository is **public**.

- Never commit a secret, token, password or real `DATABASE_URL`. Keep local values in `.env`, which git ignores.
- Never put real user data in tests or fixtures; make it up.
- The `secrets` check scans the whole history. If a secret does get pushed, **revoke or rotate it first**, then tell the team. Removing the commit does not make it safe again.

## Issues

- Ticket titles are `PUR-NNN: <imperative>` (for example `PUR-003: Create the Purchase database through run-once migrations`). Use the *Ticket* template.
- Each ticket has a header line (phase, parent, depends on, PRs), a **Files** table, Why, What to do per PR, Tests, Done when and Not in scope.
- Label a ticket `blocked` while it waits on another ticket.

## Releases

`main` is what gets released; there are no release branches. Deployment is set up in #15. Once it lands, its documentation describes how a release is run and rolled back.

## Repository settings (admins)

These settings back the rules above. The ruleset lives in [`.github/rulesets/protect-main.json`](.github/rulesets/protect-main.json); change it by PR, then an admin applies it:

```bash
gh api -X PUT repos/cs403bkk-2026/spacey-purchase/rulesets/24708742 --input .github/rulesets/protect-main.json
gh api -X PATCH repos/cs403bkk-2026/spacey-purchase \
  -F allow_merge_commit=false -F allow_rebase_merge=false -F allow_squash_merge=true \
  -f squash_merge_commit_title=PR_TITLE -f squash_merge_commit_message=PR_BODY \
  -F allow_update_branch=true -F delete_branch_on_merge=true
```

Also on: secret scanning with push protection (*Settings → Code security*).
