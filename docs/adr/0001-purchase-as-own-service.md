---
status: accepted
date: 2026-10-08
---

# Purchase is its own service in its own repository

Our supervisor directed that Spacey move from one monolith to multiple repositories, one per microservice, so Purchase moves out of `spacey` into `spacey-purchase` instead of staying a module there. This lets the Purchase team test, release and roll back without waiting on `spacey` releases; Purchase reaches Payment and Access only through their APIs and treats them as black boxes; and Purchase will own its data (`users`, `spaces`, `bookings`) in its own database. It follows the same direction as `spacey`'s [ADR 0002](https://github.com/cs403bkk-2026/spacey/blob/main/docs/adr/0002-separate-frontend-repository.md), which moved the browser client to its own repository. Plan: [cs403bkk-2026/spacey-purchase#3](https://github.com/cs403bkk-2026/spacey-purchase/issues/3), moved here from `spacey` #248.

## Considered Options

- **Modular monolith** (keep `purchase/` in `spacey` behind its boundary files): one repo, deploy and database, but releases and rollbacks stay shared with every other team.
- **Monorepo with separate deploy units**: separate releases and atomic cross-service changes, but CI, branch protection and review stay shared.

Neither was taken: the supervisor set multi-repo microservices.

## Phases 1–4 are done in this repository

The plan's Phases 1–4 (PUR-001 to PUR-006) were first written for `spacey`. They are done here instead, and the issues moved with the plan (#3, #4 to #12) on 2026-10-08, with the team lead's approval. The tickets stay the spec for what to build; only what the move forces changed:

- **Import first.** PUR-009 brings `purchase/` in with its history, plus unedited reference copies of `spacey`'s `app.py`, `tests/test_app.py` and `openapi.yaml` that PUR-001 and PUR-002 edit. PUR-007 no longer imports.
- **"Live" means "merged" until PUR-007.** This repository serves no production traffic before the cutover, so PUR-001 to PUR-006 reach production together at PUR-007.
- **Migrations stay additive until PUR-008.** `spacey` still runs its own `schema.py` on the same database, so the drops in PUR-003 PR 2 wait for PUR-008.
- **Fixes land only here.** `spacey`'s `purchase/` is not changed, so known bugs in it, including the free booking that PUR-001 fixes, stay live until PUR-007.
- **`shared/`, `payment/` and `access.py` are not copied.** Imported Purchase tests fail on those imports until the modules are available, so PUR-001 to PUR-006 cannot meet their `pytest -q` gate for those tests.
