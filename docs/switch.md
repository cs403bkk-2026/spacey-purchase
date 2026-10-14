# Switching production to the Purchase service

The runbook for PUR-025 (#37). It moves production's Purchase reads and writes
from spacey's old tables to this service, and back again if needed. Every step
runs with the team's own logins. It needs no owner login, no change to spacey's
Nomad variable at switch time and no shell in an allocation.

## How the switch works

At each start the hub (spacey) creates the one-row table `purchase_switch` in
spacey's database and keeps the freeze trigger on `users`, `spaces` and
`bookings` installed. Only `team_purchase` logins may change the row. The hub
reads it on every Purchase call, so a change takes effect at once, with no
restart.

| Row | The hub (with `PURCHASE_URL` set) | The old tables |
|---|---|---|
| `off` | runs the old SQL, as before the switch | take every write |
| `frozen` | runs the old SQL | refuse every write except the mirror's and the repair's: booking, cancel, register and space writes answer `503 {"error": "purchase unavailable"}`, `/pay` answers `500 {"error": "payment unavailable"}` |
| `on` | calls this service | refuse every write except the mirror's |

| Script | Sets | Refused |
|---|---|---|
| `scripts/freeze.sql` | `frozen`, after the writes in flight finish | never |
| `scripts/switch_on.sql` | `on` | from `off` |
| `scripts/unfreeze.sql` | `off` | from `on` |

The platform stores `purchase_url` and `purchase_token` in `nomad/jobs/spacey`
once. Until the row is `on` they change nothing. The hub logs
`purchase service check: ok` at each start once they are there (gate G5).

## Values

| Value | What it is |
|---|---|
| `$HUB` | `https://spacey.cs403bkk26.space` |
| `$SERVICE_URL` | `https://purchase.cs403bkk26.space` |
| `$OLD_URL` | your own login (a member of `team_purchase`) on spacey's DB: `host=postgres.cs403bkk26.space port=5432 dbname=spacey user=<you> sslmode=verify-full`. Type the password at the prompt; never paste it anywhere |
| `$NEW_URL` | the same with `dbname=purchase`, where your login acts as `team_purchase`, the new tables' owner |
| `$PURCHASE_TOKEN` | `PURCHASE_TOKEN=$(nomad var get -namespace=purchase -item=purchase_token nomad/jobs/purchase)`. Never paste it |
| Nomad token | your own: logs of jobs `spacey` (namespace `startup`) and `purchase`, and `nomad/jobs/purchase` |
| `psql` | with SSL, always `-v ON_ERROR_STOP=1` (without it, psql exits 0 when a statement fails). If `verify-full` finds no CA store, add `sslrootcert=<your CA bundle>` to the URL |
| The copy script | this repo at the revision our service runs, after `uv sync --locked`: `uv run python scripts/copy_from_spacey.py "$OLD_URL" "$NEW_URL" <mode>` |

```sh
export HUB=https://spacey.cs403bkk26.space SERVICE_URL=https://purchase.cs403bkk26.space
export OLD_URL="host=postgres.cs403bkk26.space port=5432 dbname=spacey user=<you> sslmode=verify-full"
export NEW_URL="${OLD_URL/dbname=spacey/dbname=purchase}"
copy() { uv run python scripts/copy_from_spacey.py "$OLD_URL" "$NEW_URL" "$@"; }
sql() { psql -v ON_ERROR_STOP=1 "$OLD_URL" "$@"; }
state() { sql -Atc 'SELECT state FROM purchase_switch'; }
```

## Gates (on the switch day, all green)

| Gate | Check | Green |
|---|---|---|
| G1 The hub has PUR-025's spacey PR | in a spacey clone: `git fetch -q && git merge-base --is-ancestor <its merge commit> $(curl -s $HUB/health \| jq -r .revision) && echo ok` | `ok` |
| G2 Our service runs this runbook's PR | in this repo: the same with `<its merge commit>` and `$SERVICE_URL/health` | `ok` |
| G3 Logins | `sql -Atc "SELECT state, has_table_privilege('purchase_switch', 'UPDATE') FROM purchase_switch"`, then `psql -v ON_ERROR_STOP=1 "$NEW_URL" -Atc 'SELECT count(*) FROM bookings'` | `off\|t`, then `0` |
| G4 Rehearsal | `git switch --detach $(curl -s $SERVICE_URL/health \| jq -r .revision)`, then `scripts/rehearse_switch.sh $(curl -s $HUB/health \| jq -r .revision)` | `rehearsal passed` |
| G5 Armed, and spacey reaches us | `nomad alloc logs -namespace=startup -stderr -job spacey spacey \| grep 'purchase service check' \| tail -1`. No line yet means the platform has not stored the URL: wait | `INFO purchase service check: ok` |
| G6 `/pay` charges locally | in a spacey clone: `git grep -nE '^from payment.services import .*authorize_card' $(curl -s $HUB/health \| jq -r .revision) -- purchase/booking.py` | one line |

Pick a quiet 15-minute window and tell the team. From step 3 to step 6,
writes answer 503 and `/pay` answers 500; reads keep working.

## Switch

Fill in the log template as you go.

1. The test account (a password of 8 or more characters). Note its id.

   ```sh
   J='{"email":"switch-test@example.test","password":"<password>"}'
   curl -s -c jar -H 'Content-Type: application/json' -d "$J" $HUB/register
   curl -s -c jar -b jar -H 'Content-Type: application/json' -d "$J" $HUB/login
   ```

2. The live counts, then the dry run. It prints `anomalies: none`;
   `completed` equals `paid_bookings`; `expired` plus `pending` equals
   `unpaid_bookings`. If `/metrics` changed in between, run both again. Note
   `expired N (F future slots free)`: from step 6 on, those F slots can be booked.

   ```sh
   curl -s $HUB/metrics | jq -c '{paid_bookings, unpaid_bookings}'
   copy --dry-run
   ```

3. Freeze. The row reads `frozen`, a booking answers 503 and `/bookings` 200.

   ```sh
   sql -f scripts/freeze.sql && state
   S=$(curl -s $HUB/spaces | jq '.spaces[0].id')
   SLOT='{"member":"switch test","start_time":"2035-01-01T09:00:00Z","end_time":"2035-01-01T10:00:00Z"}'
   curl -s -w ' %{http_code}\n' -b jar -H 'Content-Type: application/json' -d "$SLOT" $HUB/spaces/$S/bookings
   curl -s -o /dev/null -w '%{http_code}\n' $HUB/bookings
   ```

4. Save the answers.

   ```sh
   curl -s $HUB/bookings | jq -S '.bookings | sort_by(.id)' > before-bookings.json
   curl -s $HUB/metrics | jq -S 'del(.utilization)' > before-metrics.json
   ```

5. Copy: `VERIFY OK` twice, and `inserted 0` for each table the second time.

   ```sh
   copy --apply && copy --verify && copy --apply && copy --verify
   ```

6. Switch on. The row reads `on`; the hub's next call goes to the service.

   ```sh
   sql -f scripts/switch_on.sql && state
   ```

7. Compare: both diffs print nothing, except bookings made after step 6
   (`utilization` is left out: expired holds no longer count as booked hours).
   Our service's log shows the hub's `GET /bookings`.

   ```sh
   diff <(curl -s $HUB/bookings | jq -S '.bookings | sort_by(.id)') before-bookings.json
   diff <(curl -s $HUB/metrics | jq -S 'del(.utilization)') before-metrics.json
   nomad alloc logs -namespace=purchase -job purchase app | grep '"GET /bookings ' | tail -1
   ```

8. The hold: if the dry run counted expired bookings, the oldest unpaid booking
   answers `409 {"error":"booking hold has expired"}`.

   ```sh
   E=$(jq '[.[] | select(.paid | not)][0].id' before-bookings.json)
   curl -s -w ' %{http_code}\n' -H "Authorization: Bearer $PURCHASE_TOKEN" $SERVICE_URL/bookings/$E/payable
   ```

9. The test journey: the same id at login, a booking id above 100000, then
   pay, unlock and cancel answer 200, the cancel with `"paid": true` and its
   `amount_cents`.

   ```sh
   curl -s -c jar -b jar -H 'Content-Type: application/json' -d "$J" $HUB/login
   B=$(curl -s -b jar -H 'Content-Type: application/json' -d "$SLOT" $HUB/spaces/$S/bookings | jq .id); echo $B
   curl -s -w ' %{http_code}\n' -H 'Content-Type: application/json' \
     -d '{"card_number":"4242424242424242","expiry":"12/30","cvc":"123"}' $HUB/bookings/$B/pay
   curl -s -w ' %{http_code}\n' -X POST $HUB/bookings/$B/unlock
   curl -s -w ' %{http_code}\n' -X DELETE $HUB/bookings/$B
   ```

10. The mirror: `copy --repair` prints `inserted 0, updated 0, deleted 0` for
    `users`, `spaces` and `bookings`. It locks the old tables for well under a
    second.

11. Grafana's numbers: the two lines are equal.

    ```sh
    sql -Atc "SELECT (SELECT count(*) FROM spaces), count(*) FILTER (WHERE paid), COALESCE(sum(amount_cents) FILTER (WHERE paid), 0) FROM bookings"
    curl -s $HUB/metrics | jq -r '"\(.spaces)|\(.paid_bookings)|\(.revenue_cents)"'
    ```

12. Leave the row `on`. Post the log on #37: counts, ids and times only.

**Abort before step 6:** `sql -f scripts/unfreeze.sql`, then
`psql -v ON_ERROR_STOP=1 "$NEW_URL" -c 'TRUNCATE bookings, subscriptions, users, spaces'`.
The new sequences keep their gap. Try again another day from step 2.

**A check in steps 7 to 11 fails:** Rollback.

## Rollback (possible until PUR-026)

Every write passes the hub, so the mirror has kept the old tables current.
Nothing is copied back. Keep this order: a service write after the repair could
get an id that the old sequence hands out again.

1. `sql -f scripts/freeze.sql && state`: `frozen`. From the hub's next call,
   reads come from the old tables, writes answer 503 and `/pay` 500.
2. Wait 10 seconds, so the service calls in flight (1.5 s each) finish.
3. `copy --repair`, twice. The second run prints 0 everywhere. The repair adds
   any write the mirror missed and moves the old id sequences above every id
   the service gave out.
4. `sql -f scripts/unfreeze.sql && state`: `off`. Writes work again.
5. Book a far-future slot as the test account and cancel it. Its id is above
   `psql -v ON_ERROR_STOP=1 "$NEW_URL" -Atc 'SELECT max(id) FROM bookings'`.
6. Post the log. The hold is off again; an expired hold whose slot was booked
   again stays deleted from the old tables.

To switch again: `TRUNCATE bookings, subscriptions, users, spaces` on
`$NEW_URL`, then Switch from step 2. New ids jump by another 100,000.

**If the platform removes `purchase_url` while the row is `on`:** after
spacey's restart the hub runs the old SQL. The freeze refuses its writes (503),
and reads come from the old tables, which the mirror kept current. Do the
Rollback, or wait until the URL is back.

## The stable period

The course ends on 15 October 2026, so stable is not a number of days
(product owner, 14 October 2026). It holds when:

1. Switch steps 7 to 11 passed.
2. The product owner's hand test on the real app passed: register, log in,
   book, pay, unlock and cancel, each as a user would.
3. spacey's log has no `purchase mirror failed` line, or `copy --repair` fixed
   each one:
   `nomad alloc logs -namespace=startup -stderr -job spacey spacey | grep -e 'purchase mirror failed' -e 'purchase service check' | tail`.

Then close #37. Until PUR-026 is live, check that log once a day. A new
`purchase mirror failed` line: run `copy --repair` and post its three count
lines on #37. A `check: failed` line after a restart of spacey: check
`$SERVICE_URL/health`, and do the Rollback if Purchase routes answer 503.

## Rehearsal

`scripts/rehearse_switch.sh <HUB_REF>` runs every step above on local
databases with production's roles, against spacey at `HUB_REF` and this
service, and ends with `rehearsal passed`. It needs Docker, curl and jq.
`COMPOSE_FILE=compose.switch.yaml:<override>` builds the hub from a local
spacey checkout instead of GitHub.

## Log template

```
PUR-025 switch, <date>, window <start>-<end> UTC, by <login>
Gates: G1 ok, G2 ok (service <sha>), G3 off|t and 0, G4 rehearsal passed (service <sha>, hub <sha>), G5 check: ok (<time>), G6 one line
1   test account id <id>
2   <time> dry run: users <n> spaces <n> subscriptions <n> bookings <n> (completed <n>, expired <n> (<f> future slots free), pending <n>), anomalies none; /metrics paid <n> unpaid <n>
3   <time> frozen; booking 503; /bookings 200
5   <time> apply: inserted <n>/<n>/<n>/<n>; sequences <n>/<n>/<n>; VERIFY OK; second apply inserted 0; VERIFY OK
6   <time> on
7   diffs empty; service GET /bookings at <time>
8   booking <id>: payable 409
9   test booking <id>: pay 200, unlock 200, cancel 200 (paid true, amount_cents <n>)
10  repair: 0/0/0 for users, spaces, bookings
11  old tables <spaces>|<paid>|<revenue> = /metrics
12  row kept on
```
