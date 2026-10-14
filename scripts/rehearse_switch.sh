#!/usr/bin/env bash
# Rehearse docs/switch.md on local databases: PUR-025's Tests, in order.
# Usage: scripts/rehearse_switch.sh <HUB_REF>   (a spacey commit; needs docker, curl, jq)
# COMPOSE_FILE=compose.switch.yaml:<override> builds the hub from a local checkout.
# Stops at the first failure; removes its containers and volumes on exit.
set -euo pipefail
cd "$(dirname "$0")/.."
export HUB_REF=${1:?usage: scripts/rehearse_switch.sh <HUB_REF>}
export COMPOSE_FILE=${COMPOSE_FILE:-compose.switch.yaml}
unset PURCHASE_URL PURCHASE_TOKEN # the compose defaults: armed, with the right token
HUB=http://127.0.0.1:8000
OLD_URL=postgresql://student:rehearsal@olddb/spacey # a team login, as in production
CARD='{"card_number":"4242424242424242","expiry":"12/30","cvc":"123"}'
T=$(mktemp -d)
trap 'docker compose down -v >/dev/null 2>&1; rm -rf "$T"' EXIT

check() { CHECK=$1; echo "== $1"; }
fail() { echo "FAILED $CHECK: $*" >&2; exit 1; }

# req METHOD PATH [JSON]: prints the status; the body lands in $T/body, cookies in $T/$JAR.
req() {
  local args=(-s -o "$T/body" -w '%{http_code}' -X "$1" -b "$T/$JAR" -c "$T/$JAR")
  [ $# -lt 3 ] || args+=(-H 'Content-Type: application/json' -d "$3")
  curl "${args[@]}" "$HUB$2"
}
want() { # want STATUS METHOD PATH [JSON]
  local status
  status=$(req "${@:2}")
  [ "$status" = "$1" ] || fail "$2 $3 answered $status, want $1: $(cat "$T/body")"
}
got() { jq -r "$1" "$T/body"; }
is() { [ "$(got "$1")" = "$2" ] || fail "$1 is $(got "$1"), want $2: $(cat "$T/body")"; }
slot() { printf '{"member":"rehearsal","start_time":"2031-01-%02dT09:00:00Z","end_time":"2031-01-%02dT10:00:00Z"}' "$1" "$1"; }
user() { printf '{"email":"rehearsal-%s@example.test","password":"rehearsal-%s-pass"}' "$1" "$1"; }

# The runbook's tools. psql runs in olddb's container, as the login named.
psql_as() { docker compose exec -T olddb psql -v ON_ERROR_STOP=1 -qAt "postgresql://$1:rehearsal@localhost/spacey" "${@:2}"; }
team() { psql_as student "$@"; }
newdb() { docker compose exec -T newdb psql -v ON_ERROR_STOP=1 -qAt -U postgres "$@"; }
copy() { docker compose exec -T purchase sh -c 'python scripts/copy_from_spacey.py "$1" "$DATABASE_URL" "$2"' copy "$OLD_URL" "$1"; }
healthy() {
  for _ in $(seq 60); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' $HUB/health)" = 200 ] && return
    sleep 1
  done
  fail "hub not healthy"
}
link_line() { # the hub's own check of the link, logged once it starts (G5)
  for _ in $(seq 30); do
    docker compose logs --no-log-prefix hub 2>/dev/null | grep -o 'purchase service check: .*' && return
    sleep 1
  done
  fail "no 'purchase service check' line in the hub's log"
}
hub_with() { # hub_with VAR=VALUE...: the platform's variable change, and its restart
  env "$@" docker compose up -d hub >"$T/compose.log" 2>&1 || fail "$(cat "$T/compose.log")"
  healthy
}
snap() { # Switch step 4
  curl -s $HUB/bookings | jq -S '.bookings | sort_by(.id)' >"$T/$1-bookings.json"
  curl -s $HUB/metrics | jq -S 'del(.utilization)' >"$T/$1-metrics.json"
}
same_as() { # Switch step 7's diffs
  diff <(curl -s $HUB/bookings | jq -S '.bookings | sort_by(.id)') "$T/$1-bookings.json" >&2 || fail "/bookings differ from $1"
  diff <(curl -s $HUB/metrics | jq -S 'del(.utilization)') "$T/$1-metrics.json" >&2 || fail "/metrics differ from $1"
}
state() { team -c 'SELECT state FROM purchase_switch'; }
old_rows() { team -c "SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM spaces), md5((SELECT string_agg(b::text, ',' ORDER BY id) FROM bookings b))"; }
max_new() { newdb -c "SELECT max(id) FROM $1"; }
count() { sed -n "s/^$1 \([0-9]*\).*/\1/p" "$T/dry"; }
dry_run_clean() { # Switch step 2: no anomaly; the counts match /metrics
  copy --dry-run >"$T/dry" || fail "dry run: $(cat "$T/dry")"
  grep -qx 'anomalies: none' "$T/dry" || fail "dry run: $(cat "$T/dry")"
  want 200 GET /metrics
  [ "$(count 'paid=true -> completed')" = "$(got .paid_bookings)" ] || fail "completed is not $(got .paid_bookings): $(cat "$T/dry")"
  [ $(($(count 'paid=false -> expired') + $(count 'paid=false -> pending'))) = "$(got .unpaid_bookings)" ] ||
    fail "expired + pending is not $(got .unpaid_bookings): $(cat "$T/dry")"
}
copy_exact() { # Switch step 5
  copy --apply >"$T/apply" || fail "apply: $(cat "$T/apply")"
  copy --verify | grep -qx 'VERIFY OK' || fail "verify after the first apply"
  copy --apply >"$T/apply2" || fail "second apply: $(cat "$T/apply2")"
  [ "$(grep -c 'inserted 0$' "$T/apply2")" = 4 ] || fail "second apply inserted rows: $(cat "$T/apply2")"
  copy --verify | grep -qx 'VERIFY OK' || fail "verify after the second apply"
}
switched_same() { # switch_changes_no_answer: no restart, the next request is the service's
  team -f - <scripts/switch_on.sql
  [ "$(state)" = on ] || fail "state is $(state)"
  same_as "$1"
  docker compose logs --no-log-prefix purchase >"$T/service.log"
  grep -q '"GET /bookings ' "$T/service.log" || fail "the service saw no GET /bookings"
  JAR=a
  want 200 GET /me/bookings
  is '.bookings | length' 3
  want 200 POST /login "$(user a)"
  is .id "$A"
}

echo "building the hub at $HUB_REF and this service"
docker compose build -q
docker compose up -d --wait olddb newdb >"$T/compose.log" 2>&1 || fail "$(cat "$T/compose.log")"
# Production's roles: spacey owns the old tables and is no superuser; every
# student login gets read-write on spacey's new tables; team_purchase is ours.
docker compose exec -T olddb psql -v ON_ERROR_STOP=1 -q -U postgres >/dev/null <<'SQL'
CREATE ROLE spacey LOGIN PASSWORD 'rehearsal';
CREATE DATABASE spacey OWNER spacey;
CREATE ROLE spacey_students;
CREATE ROLE team_purchase;
CREATE ROLE student LOGIN PASSWORD 'rehearsal' IN ROLE spacey_students, team_purchase;
CREATE ROLE other LOGIN PASSWORD 'rehearsal' IN ROLE spacey_students;
\c spacey
ALTER DEFAULT PRIVILEGES FOR ROLE spacey IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO spacey_students;
ALTER DEFAULT PRIVILEGES FOR ROLE spacey IN SCHEMA public
    GRANT SELECT, UPDATE, USAGE ON SEQUENCES TO spacey_students;
SQL
docker compose up -d --wait purchase hub >"$T/compose.log" 2>&1 || fail "$(cat "$T/compose.log")"
healthy

check journey_before # armed, switch off: the old SQL answers, the service gets nothing
[ "$(state)" = off ] || fail "state is $(state)"
JAR=a
want 201 POST /register "$(user a)"
A=$(got .id)
want 200 POST /login "$(user a)"
want 200 GET /spaces
S=$(got '.spaces[0].id')
PRICE=$(got '.spaces[0].price_cents') # a slot is one hour
for n in 1 2 3 4; do
  want 201 POST "/spaces/$S/bookings" "$(slot $n)"
  declare "B$n=$(got .id)"
done
want 200 POST "/bookings/$B1/pay" "$CARD"
want 200 POST "/bookings/$B1/unlock"
want 200 DELETE "/bookings/$B4"
want 200 GET /me/bookings
is '.bookings | length' 3
[ "$(newdb -c 'SELECT count(*) FROM bookings')" = 0 ] || fail "the service took a write"
team -c "UPDATE bookings SET created_at = created_at - interval '16 minutes' WHERE id = $B2"

check link_check_is_in_the_log # G5 without exec: the hub logs its own check
[ "$(link_line)" = 'purchase service check: ok' ] || fail "$(link_line)"
hub_with PURCHASE_TOKEN=wrong
[ "$(link_line)" = 'purchase service check: failed (GET /metrics: 403)' ] || fail "$(link_line)"
hub_with
[ "$(link_line)" = 'purchase service check: ok' ] || fail "$(link_line)"

check only_the_team_sets_the_switch
psql_as other -c "UPDATE purchase_switch SET state = 'frozen'" 2>"$T/err" && fail "a non-team login changed the switch"
grep -q 'permission denied' "$T/err" || fail "$(cat "$T/err")"
[ "$(psql_as other -c 'SELECT state FROM purchase_switch')" = off ] || fail "a non-team login cannot read the switch"
team -f - <scripts/switch_on.sql 2>"$T/err" && fail "switch_on.sql ran before the freeze"
grep -q 'not frozen' "$T/err" || fail "$(cat "$T/err")"

check dry_run_is_clean
dry_run_clean
grep -qx 'paid=true -> completed 1' "$T/dry" || fail "$(cat "$T/dry")"
grep -qx 'paid=false -> expired 1 (1 future slots free)' "$T/dry" || fail "$(cat "$T/dry")"
grep -qx 'paid=false -> pending 1' "$T/dry" || fail "$(cat "$T/dry")"

check freeze_refuses_writes
team -f - <scripts/freeze.sql
[ "$(state)" = frozen ] || fail "state is $(state)"
before=$(old_rows)
want 503 POST "/spaces/$S/bookings" "$(slot 5)"
is .error 'purchase unavailable'
want 503 DELETE "/bookings/$B2"
is .error 'purchase unavailable'
JAR=x
want 503 POST /register "$(user x)"
is .error 'purchase unavailable'
JAR=a
want 500 POST "/bookings/$B3/pay" "$CARD"
is .error 'payment unavailable'
[ "$(old_rows)" = "$before" ] || fail "old rows changed"
want 200 GET /bookings
want 200 GET "/bookings/$B1"
want 200 GET /spaces
want 200 POST "/bookings/$B1/unlock"
docker compose restart hub >/dev/null 2>&1
healthy
[ "$(old_rows)" = "$before" ] || fail "the hub's restart changed old rows"
snap before

check copy_is_exact
copy_exact

check switch_changes_no_answer
switched_same before

check hold_applies_from_the_switch
want 200 GET "/bookings/$B2"
is .paid false
want 200 GET "/spaces/$S/bookings"
is "any(.bookings[]; .id == $B2)" false
want 409 POST "/bookings/$B2/pay" "$CARD"
is .error 'booking hold has expired'
want 201 POST "/spaces/$S/bookings" "$(slot 2)"
[ "$(team -c "SELECT count(*) FROM bookings WHERE id = $B2")" = 0 ] || fail "olddb still has B2"

check journey_after
want 200 POST "/bookings/$B3/pay" "$CARD"
is .paid true
JAR=c
want 201 POST /register "$(user c)"
C=$(got .id)
is '.id > 100000' true
want 200 POST /login "$(user c)"
want 201 POST "/spaces/$S/bookings" "$(slot 6)"
B5=$(got .id)
is '.id > 100000' true
want 200 POST "/bookings/$B5/pay" "$CARD"
want 200 POST "/bookings/$B5/unlock"
want 200 DELETE "/bookings/$B5"
is .paid true
is .amount_cents "$PRICE"
want 404 GET "/bookings/$B5"
want 200 GET /bookings
is "any(.bookings[]; .id == $B5)" false

check mirror_keeps_old_current
copy --repair >"$T/repair" || fail "repair: $(cat "$T/repair")"
[ "$(grep -c 'inserted 0, updated 0, deleted 0$' "$T/repair")" = 3 ] || fail "repair changed rows: $(cat "$T/repair")"
want 200 GET /metrics
[ "$(team -c "SELECT (SELECT count(*) FROM spaces) || '|' || count(*) FILTER (WHERE paid) || '|' || COALESCE(sum(amount_cents) FILTER (WHERE paid), 0) FROM bookings")" = \
  "$(got '"\(.spaces)|\(.paid_bookings)|\(.revenue_cents)"')" ] || fail "old tables differ from /metrics"

check lost_url_fails_loudly
hub_with PURCHASE_URL=
before=$(old_rows)
want 503 POST "/spaces/$S/bookings" "$(slot 7)"
JAR=y
want 503 POST /register "$(user y)"
want 200 GET /bookings
want 200 GET /spaces
[ "$(old_rows)" = "$before" ] || fail "old rows changed"
hub_with
[ "$(link_line)" = 'purchase service check: ok' ] || fail "$(link_line)"

check rollback_keeps_every_change
JAR=c
want 201 POST "/spaces/$S/bookings" "$(slot 8)"
snap last
team -f - <scripts/unfreeze.sql 2>"$T/err" && fail "unfreeze.sql ran while the switch was on"
team -f - <scripts/freeze.sql # Rollback step 1: no restart, the next request is the old SQL's
want 503 POST "/spaces/$S/bookings" "$(slot 9)"
sleep 5 # the calls in flight (1.5 s each) finish
copy --repair >"$T/repair" || fail "repair: $(cat "$T/repair")"
copy --repair >"$T/repair2" || fail "second repair: $(cat "$T/repair2")"
[ "$(grep -c 'inserted 0, updated 0, deleted 0$' "$T/repair2")" = 3 ] || fail "second repair changed rows: $(cat "$T/repair2")"
team -f - <scripts/unfreeze.sql
[ "$(state)" = off ] || fail "state is $(state)"
diff <(curl -s $HUB/bookings | jq -S '.bookings | sort_by(.id)') \
  <(jq "map(select(.id != $B2))" "$T/last-bookings.json") >&2 || fail "/bookings differ from the service's last list"
want 404 GET "/bookings/$B5"
want 200 POST /login "$(user c)"
is .id "$C"

check no_id_reused
JAR=d
want 201 POST /register "$(user d)"
is ".id > $(max_new users)" true
want 201 POST "/spaces/$S/bookings" "$(slot 10)"
B6=$(got .id)
is ".id > $(max_new bookings)" true

check switch_again
newdb -c 'TRUNCATE bookings, subscriptions, users, spaces'
team -f - <scripts/freeze.sql
dry_run_clean
snap again
copy_exact
switched_same again
JAR=c
want 201 POST "/spaces/$S/bookings" "$(slot 11)"
is ".id > $B6" true

echo "rehearsal passed"
