# Deploying the Purchase service

Deployment is enabled. From PUR-016 on, the service needs `DATABASE_URL` and
`PURCHASE_TOKEN` to start, applies its migrations at start, and `GET /health`
runs `SELECT 1`, so a healthy check also covers the database. No data copy or
traffic switch runs in this workflow (those are PUR-017 and PUR-025).

## Service address and deployed revision

| | |
|---|---|
| Address | `https://purchase.cs403bkk26.space` |
| Health | `GET /health` → `{"status": "ok", "revision": "<git sha>"}`, or `503` with `{"status": "error", "error": "database unreachable"}` from PUR-016 on |
| Deployed revision | `4b6b1b9565ed956e9336d65da9d65f5f6fa9f60e` (PR #66, PUR-024), image `ghcr.io/cs403bkk-2026/spacey-purchase@sha256:37627dffbe664efcacde27cbf2936029d1ba50524a60ffc276bf47274f553c15`, [release run 38519721874](https://github.com/cs403bkk-2026/spacey-purchase/actions/runs/38519721874), `/health` reported it at 2026-10-14T06:12:34Z |

Update this table after each verified release: source SHA, image digest,
workflow run URL and the time `/health` reported that SHA.

## Names

| Name | Value |
|---|---|
| `NS` (Nomad namespace) | `purchase` |
| `JOB` (Nomad job) | `purchase` |
| `VAR` (runtime variable) | `nomad/jobs/purchase` |
| `HOST` | `purchase.cs403bkk26.space` |

The job file is [`deploy/purchase.nomad.hcl`](../deploy/purchase.nomad.hcl). Its
runtime variable lives under the job-owned path so the job's workload identity
can read it without broader variable permissions.

## Runtime variable items

| Item | Value |
|---|---|
| `database_url` | The Purchase database and login, never spacey's. The platform owner runs `CREATE EXTENSION btree_gist` in it once; without it the first migration fails and nothing is applied. |
| `purchase_token` | 64 hex characters (`python3 -c 'import secrets; print(secrets.token_hex(32))'`). Every request except `GET /health` must send `Authorization: Bearer <purchase_token>`. Only the hub calls this service: PUR-025 puts the same value in spacey's runtime variable. Payments' callback goes to the hub, not here (PUR-027). Never paste it in an issue, PR or chat. |

The platform owner stores `database_url`. Our SRE stores `purchase_token`
(PUR-024). Never pass either as a job argument, commit it, or copy it into
GitHub. The service has no `PAYMENTS_URL` or `SERVICE_TOKEN`: PUR-027 gives
those to the hub.

The job renders both items with `error_on_missing_key = true`, so a missing item
fails the release. Every push to `main` releases, so store a new item before
merging the job file change that reads it. The job never sets
`PURCHASE_TEST_RESET` or `SEED_STARTER_SPACE`, and it runs the image's own
command, with one thread per worker (`tests/test_deploy_config.py`).

## Platform setup

The platform owner must confirm the names above before creating anything, then
checks:

- The namespace, scoped deployment token and job-owned variable exist.
- Traefik watches the namespace and its discovery policy permits reading it.
- DNS and TLS reach the course ingress for the approved hostname.
- Nomad clients can pull the image from GHCR, using public package visibility
  or separately provisioned registry authentication.
- The application node can reach the database address in `database_url`, and
  the connection settings match what that server offers.
- GitHub's `production` environment does not require a separate release approval.

Set `DEPLOY_ENABLED=true` as a repository variable only after setup and review.
In the protected `production` environment set variables `NOMAD_NAMESPACE`,
`APP_HOSTNAME` and `RUNTIME_VARIABLE` to the approved names, and secrets
`NOMAD_ADDR` and a fresh namespace-scoped `NOMAD_TOKEN`.

## Release

`main` is what gets released. Every push to `main` starts the `release` workflow.
The release owner can also retry it from `main` (Actions → release → Run workflow).
With `DEPLOY_ENABLED=true`, deployment proceeds automatically after the build
checks pass, without a separate production approval. The workflow runs the
tests, builds the image with the commit as its revision, checks that the image's
`/health` reports that commit, and publishes that same image. Deployment uses its
registry digest, waits for Nomad's rollout, then requires the public `/health` to
report `status: ok` and the exact commit. A superseded `main` revision is
rejected.

For an approved manual release, export `NOMAD_ADDR`, `NOMAD_TOKEN`, `NS`, `HOST`,
`VAR`, plus the reviewed `REVISION` and immutable `IMAGE` reference, then run:

```bash
nomad job run -var="image=${IMAGE}" -var="revision=${REVISION}" \
  -var="namespace=${NS}" -var="hostname=${HOST}" \
  -var="runtime_variable=${VAR}" deploy/purchase.nomad.hcl
curl --fail --silent --show-error "https://${HOST}/health" |
  jq -e --arg revision "${REVISION}" '.status == "ok" and .revision == $revision'
```

## Rollback

Nomad reverts an unhealthy update to the previous stable job version; the first
deployment has none. Public routing can fail even after a healthy allocation. In
either case the release owner inspects the failed deployment and either stops the
first job or redeploys the recorded previous digest and revision, then checks
`/health` again. This single-instance job does not promise uninterrupted
updates.

## Checks after a release (no traffic yet)

Until PUR-025 nothing calls the service and the Purchase database stays empty:
create no space, user or booking in it, and run the copy script only with
`--dry-run`. `SHA` is the full hash of the released commit. `T` is the token,
read without printing it:
`T=$(nomad var get -namespace="$NS" -out=json "$VAR" | jq -r .Items.purchase_token)`.

1. `nomad var get -namespace="$NS" -out=json "$VAR" | jq -c '.Items | keys'`
   prints `["database_url","purchase_token"]`.
2. `curl -s "https://$HOST/health" | jq -e --arg r "$SHA" '.status == "ok" and .revision == $r'`
   prints `true`. Check it before the next merge to `main`: each merge releases
   a newer revision.
3. `curl -s -o /dev/null -w '%{http_code}\n' "https://$HOST/spaces"` prints
   `403`. The same with `-H 'Authorization: Bearer wrong'` also prints `403`.
4. `curl -s -H "Authorization: Bearer $T" "https://$HOST/metrics" | jq -c '{spaces, bookings}'`
   prints `{"spaces":0,"bookings":0}`. In the allocation
   (`nomad alloc exec -namespace="$NS" -i -t -job "$JOB" bash`), this prints
   every table with its row count:

   ```sh
   python -c 'import os, psycopg; c = psycopg.connect(os.environ["DATABASE_URL"]); print([(t, c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]) for (t,) in c.execute("SELECT tablename FROM pg_tables WHERE schemaname = current_schema() ORDER BY 1")])'
   ```

   Expect `[('bookings', 0), ('purchase_schema_migrations', N), ('spaces', 0), ('subscriptions', 0), ('users', 0)]`,
   where `N` is the number of migration files (`ls purchase/migrations/*.sql | wc -l`).
   The same holds after the dry run.
5. Read spacey's live numbers and note the UTC time:
   `curl -s https://spacey.cs403bkk26.space/metrics | jq -c '{spaces, bookings, paid_bookings, unpaid_bookings}'`.
   In the allocation, run the dry run with a read-only login URL for spacey's
   database (`read -rs` keeps it off the screen and out of the shell history):

   ```sh
   read -rs OLD_URL    # paste the read-only URL, then press Enter
   python scripts/copy_from_spacey.py "$OLD_URL" "$DATABASE_URL" --dry-run
   ```

   Read the live numbers again; if they changed, run this step again. The dry
   run exits 0 and prints `anomalies: none` and `new 0` for every table. Its
   counts equal both live readings:

   | Dry run line | Live `/metrics` key |
   |---|---|
   | `rows spaces: old N` | `spaces` |
   | `rows bookings: old N` | `bookings` |
   | `paid=true -> completed N` | `paid_bookings` |
   | `paid=false -> expired N` plus `paid=false -> pending N` | `unpaid_bookings` |

   `users`, `subscriptions` and the sequence values have no public count:
   record them.
6. A second dry run prints the same counts while the live numbers have not
   changed. Only one move is allowed: a booking from `pending` to `expired`,
   when its 15-minute hold ran out between the two runs. The sum of the two
   stays the same.

Record counts, ids and times only, never a name, token or database URL.

## SRE check

The team SRE checks the deployment with their own access:

```bash
nomad job status -namespace=purchase purchase
curl --fail --silent --show-error https://purchase.cs403bkk26.space/health
```

The revision in the response must match the deployed revision above. Record the
actual result or blocker in issue #36 (PUR-024); platform verification alone
does not establish the SRE's access.
