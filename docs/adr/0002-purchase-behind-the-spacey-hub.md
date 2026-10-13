---
status: accepted
date: 2026-10-12
supersedes: 0001
---

# Purchase runs behind the spacey hub

ADR 0001 said Purchase reaches Payment and Access through their APIs, shares `spacey`'s database for now, and leaves `spacey`'s `purchase/` unchanged. The class decision of 9–10 October 2026 and the team's decisions of 11 October replaced all three: `spacey` stays the hub that serves production, Purchase gets its own database, and `spacey`'s `purchase/` calls this service. Plan: [cs403bkk-2026/spacey-purchase#3](https://github.com/cs403bkk-2026/spacey-purchase/issues/3).

## Hub

`spacey` is the hub: it serves production at https://spacey.cs403bkk26.space and runs every step that crosses services. This repo is the service, and only the hub calls it. Services never call each other or `spacey` (ADR 0005, proposed in `spacey#262`). The frontend and the public API see no change.

## Wrappers

`spacey/purchase/` keeps our function names and signatures. With `PURCHASE_URL` set, each of these functions (a *wrapper*) calls this service instead of running SQL. With it unset or empty, the old SQL runs, as today. The exceptions are `spacey/purchase/schema.py`, and `/pay` with `mark_booking_paid` until Payment serves `/pay` outside `spacey/purchase/`.

## Data

Purchase has its own database, built by numbered SQL migrations that each run once. The old data is copied once. A paid booking becomes `completed`. An unpaid booking created 15 minutes or more before the copy becomes `expired`, a newer one `pending`. Other rows copy as they are. Ids are kept, and new id sequences start at the old last value + 100000, so no id is ever used twice.

## Status and cancel

A booking is `pending`, `completed`, `expired` or `cancelled`. Public JSON keeps `paid` (true when `completed`) and gets no new key. A cancelled booking is kept, reads as 404, is not listed or counted, and frees its slot. The cancel answers the row as it was, for the hub's refund step. A second cancel answers 404.

## Hold

The 15-minute hold ([SP-R07](https://github.com/cs403bkk-2026/spacey-docs/blob/adc27a3/RULES.md?plain=1#L106-L110)) applies from the switch on. An unpaid booking is `expired` 15 minutes after `created_at`, and its slot is free. The hold is checked before the card is charged ([spacey ADR 0003](https://github.com/cs403bkk-2026/spacey/blob/88ec849/docs/adr/0003-booking-after-failed-payment.md?plain=1#L61-L77)): with `PURCHASE_URL` set, `mark_booking_paid` calls `check_payable` first, and an expired booking answers `409 {"error": "booking hold has expired"}` with no charge. A payment that succeeds later is confirmed only if the slot is still free; otherwise `POST /bookings/{id}/paid` answers 409 and the booking stays unpaid.

## Mirror

The new database is the source of truth. After each write the service accepted, the wrapper applies the same change to the old tables in `spacey`'s database, with the same id and in today's shape. This *mirror* keeps them current for Grafana and for Access's `/unlock`, whose codes point to `bookings` by a foreign key. A mirror failure is logged and never fails the request; `copy_from_spacey.py --repair` re-syncs. Known gap: the old booking and unpaid counts leave out each expired hold whose slot was booked again.

How each service row looks in the old tables (new → old):

| New DB | Old tables |
|---|---|
| `users` row | same id and email. The mirror hashes the same password itself, so the hash differs, but both verify |
| `spaces` row | same row, same id |
| booking `pending` | row with `paid = false` |
| booking `completed` | row with `paid = true` and its `card_last4` |
| booking `expired` | stays an unpaid row until an overlapping booking on the same space either holds its slot (`pending` or `completed`) or has a higher id. A higher id means a newer booking took the slot after this one expired. The mirror deleted this row then to make room (delete before insert), and nothing puts it back, even if that newer booking later expires or is cancelled. Old's [overlap constraint](https://github.com/cs403bkk-2026/spacey/blob/88ec849/purchase/schema.py#L111-L127) counts every row, so only one of them can stay |
| booking `cancelled` | row deleted, as spacey's cancel does today. The [`access` foreign key's cascade](https://github.com/cs403bkk-2026/spacey/blob/88ec849/purchase/schema.py#L47-L56) then deletes its access code |
| `subscriptions` | untouched |
| id sequences | the mirror keeps the service's ids and never moves old's sequences. The repair moves each to `GREATEST(old, new)` |

A paid old row is never deleted for a newer booking. So on the old tables, the booking count and the unpaid count leave out each expired hold whose slot was booked again. The paid count and revenue equal the service's `/metrics`.

## Switch and rollback

*The switch* is setting `PURCHASE_URL` (and `PURCHASE_TOKEN`) in production. It is ours alone: it waits for no other team. A trigger *freezes* the old `users`, `spaces` and `bookings`: only writes that set `purchase.mirror = 'on'` (the mirror, the repair) pass, so a lost URL fails loudly instead of writing old-style rows. Rollback: unset the URL, run `--repair` (it also moves the old id sequences past the new ids), remove the freeze. Nothing is copied back.

## End of the mirror

The old tables are not dropped in this plan. Grafana reads them until the instructor moves it. The mirror and the old SQL go only when no other code reads the old tables, no other team's foreign key points into them (the last ticket removes Access's `access_booking_id_fkey` itself, because its DDL is in `spacey/purchase/schema.py`; Access reviews), Payment serves `/pay` outside `spacey/purchase/`, and Grafana has moved.

## Subscriptions

Removed on 7 October 2026 by the product owner and all teams. Subscribe answers `410 {"error": "subscriptions have been removed"}`. The old table is copied for the record. Its 4 bookings stay `completed` at 0.

## Payment and Access

They reach bookings only through `get_booking`, `check_payable`, `mark_paid` and `check_unlockable`. Purchase calls nobody and stores only `card_last4`.

## Contract

[`openapi.yaml`](../../openapi.yaml). Every operation except `GET /health` needs `Authorization: Bearer <PURCHASE_TOKEN>`. A missing or wrong token answers 403, so the only 401 stays the login one.

## Rules for every PR

1. **Same functions in both repos.** `spacey/purchase/{booking,member,space,metrics}.py` and ours have the same function names and signatures; a ticket that adds, renames or removes one does it in both repos, and the two PRs link each other. The HTTP edge differs by nature (ours is `purchase/api.py`, the hub's is `purchase/client.py`). Hub-only: `purchase/schema.py`, `purchase/mirror.py` (until the last ticket), and `/pay` with `mark_booking_paid` (until Payment serves `/pay` outside `spacey/purchase/`); `purchase/metrics.py` is hub-only until this service serves `/metrics`.
2. **Production keeps running.** A `spacey` PR changes nothing while `PURCHASE_URL` is unset or empty, except the changes a ticket names; the hold check runs only with the URL set, and the old SQL bodies stay unchanged as the rollback path until the last ticket.
3. **Purchase calls nobody.** No Payment or Access client, token or import in this repo.
4. **Tests feed other services' answers in.** When a flow depends on another service, the test sends our endpoint the call the hub would make after each possible answer (for Payment: success, failed, unknown, refunded).
5. **Public answers stay:** paths, statuses, `{"error": "<string>"}` bodies, and the Booking JSON keys, `card_last4` included. A change keeps that error shape, never answers 401 (except login), and updates `openapi.yaml` in the same PR.
6. **Ids are never reused.** The copy keeps ids. New sequences start at the old sequence's last value + 100,000. The mirror writes old rows with the service's ids and never takes an id from the old sequences.
7. **Migrations** are numbered SQL files. Each runs once and is never edited after merge.
8. **The service first, then the mirror.** Every write goes to the service. The hub mirrors it only after the service answered success. A failed mirror write is logged. It never fails the request and never retries the service; the repair fixes it.

## Considered Options

| Considered option | Why it was not taken |
|---|---|
| ADR 0001's model | The class decision: every step that crosses services runs in the hub. |
| Freeze the old tables with no mirror | Grafana and `/unlock` would stop seeing new bookings, so the switch would wait for every other reader to move. |
| The service writes the old tables itself | The service would depend on `spacey`'s database. The hub owns it, and Purchase calls nobody (rule 3). |
| Copy old unpaid bookings as `pending`, add the hold later | Bookings past their 15 minutes would keep blocking slots, against SP-R07. |
