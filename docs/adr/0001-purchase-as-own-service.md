---
status: accepted
date: 2026-10-08
---

# Purchase is its own service in its own repository

Our supervisor directed that Spacey move from one monolith to multiple repositories, one per microservice, so Purchase moves out of `spacey` into `spacey-purchase` instead of staying a module there. This lets the Purchase team test, release and roll back without waiting on `spacey` releases; Purchase reaches Payment and Access only through their APIs and treats them as black boxes; and Purchase will own its data (`users`, `spaces`, `bookings`) in its own database. It follows the same direction as `spacey`'s [ADR 0002](https://github.com/cs403bkk-2026/spacey/blob/main/docs/adr/0002-separate-frontend-repository.md), which moved the browser client to its own repository. Plan: [cs403bkk-2026/spacey#248](https://github.com/cs403bkk-2026/spacey/issues/248).

## Considered Options

- **Modular monolith** (keep `purchase/` in `spacey` behind its boundary files): one repo, deploy and database, but releases and rollbacks stay shared with every other team.
- **Monorepo with separate deploy units**: separate releases and atomic cross-service changes, but CI, branch protection and review stay shared.

Neither was taken: the supervisor set multi-repo microservices.

## Open question

Phases 1–4 (PUR-001 to PUR-006) will be done in this repository instead of in `spacey`. What that changes is open, to be decided by the team lead: Purchase code is imported before Phase 1 rather than after Phase 4; until traffic switches (PUR-007) a bug fix must land in both repositories; and until PUR-008 both copies share one database, so neither may change Purchase's schema alone.
