# Deploying the Purchase service

Deployment is enabled. From PUR-016 on, the service needs `DATABASE_URL` and
`PURCHASE_TOKEN` to start, applies its migrations at start, and `GET /health`
runs `SELECT 1`, so a healthy check also covers the database. No data copy or
traffic switch runs in this workflow (those are PUR-017 and PUR-025).

Do not release a revision that contains PUR-016 until PUR-024 has stored
`purchase_token` in the runtime variable and added it to the job file: without
it the service refuses to start, and Nomad reverts the release.

## Service address and deployed revision

| | |
|---|---|
| Address | `https://purchase.cs403bkk26.space` |
| Health | `GET /health` → `{"status": "ok", "revision": "<git sha>"}`, or `503` with `{"status": "error", "error": "database unreachable"}` from PUR-016 on |
| Deployed revision | `6b827779debad7efdf840f10683719fb29cda6a6` (PR #39), image `ghcr.io/cs403bkk-2026/spacey-purchase@sha256:194b32f3142ff2d9ba739a548283c67889ac2171e35009b0f6184518a113158d`, [release run 38204845453](https://github.com/cs403bkk-2026/spacey-purchase/actions/runs/38204845453), `/health` reported it at 2026-10-12T04:43:08Z |

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

The platform owner stores both items. Neither may be passed as a job argument,
committed, or copied into GitHub. The job renders both with
`error_on_missing_key = true`, so a missing item fails the release instead of
starting the service without it.

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

## SRE check

The team SRE checks the deployment with their own access:

```bash
nomad job status -namespace=purchase purchase
curl --fail --silent --show-error https://purchase.cs403bkk26.space/health
```

The revision in the response must match the deployed revision above. Record the
actual result or blocker in issue #15; platform verification alone does not
establish the SRE's access.

## Checks after a release (no traffic yet)

These checks require the release owner's production access and the SRE's
read-only Spacey database login. Do not create rows in the Purchase database.
The commands below are operational checks; run them only with the authorized
production credentials. Locally, `python scripts/copy_from_spacey.py --help`
only displays usage and does not connect to either database.

1. Confirm the allocation is running the intended release with
   `nomad job status -namespace="$NS" "$JOB"`. Compare its deployed revision
   with the reviewed `main` SHA and the source revision from the `/health`
   response below.
2. Check the public health response and its revision:

   ```bash
   curl --fail --silent --show-error "https://$HOST/health" |
     jq -e --arg revision "$REVISION" \
       '.status == "ok" and .revision == $revision'
   ```

3. Confirm a protected route rejects both an absent and incorrect token. Each
   command must print `403`:

   ```bash
   curl --silent --output /dev/null --write-out '%{http_code}\n' \
     "https://$HOST/spaces"
   curl --silent --output /dev/null --write-out '%{http_code}\n' \
     --header 'Authorization: Bearer wrong' "https://$HOST/spaces"
   ```

4. With the provisioned token available in a protected shell variable named
   `T`, confirm Purchase metrics report no spaces or bookings. Do not print or
   record `T`:

   ```bash
   curl --fail --silent --show-error --header "Authorization: Bearer $T" \
     "https://$HOST/metrics" | jq -c '{spaces, bookings}'
   ```

   Expected output: `{"spaces":0,"bookings":0}`.
5. In the Purchase allocation, inspect every table in the current schema. The
   `users`, `spaces`, `subscriptions`, and `bookings` counts must be zero;
   migration rows in `purchase_schema_migrations` are expected:

   ```bash
   python -c 'import os, psycopg; c = psycopg.connect(os.environ["DATABASE_URL"]); print([(t, c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]) for (t,) in c.execute("SELECT tablename FROM pg_tables WHERE schemaname = current_schema() ORDER BY 1")])'
   ```

6. Read Spacey's live metrics before the copy dry run and record the UTC time:

   ```bash
   curl --fail --silent --show-error \
     https://spacey.cs403bkk26.space/metrics |
     jq -c '{spaces, bookings, paid_bookings, unpaid_bookings}'
   ```

   From a Purchase allocation, start a shell, enter the read-only Spacey
   database URL without echoing it, and run only `--dry-run`. `DATABASE_URL`
   in the allocation already names Purchase. Never put either URL in command
   history or a report:

   ```bash
   nomad alloc exec -namespace="$NS" -i -t -job "$JOB" bash
   read -rs OLD_URL
   python scripts/copy_from_spacey.py "$OLD_URL" "$DATABASE_URL" --dry-run
   unset OLD_URL
   ```

7. Read Spacey's `/metrics` again using the command above. Compare both live
   readings with the dry-run counts: old spaces to `spaces`, old bookings to
   `bookings`, completed to `paid_bookings`, and expired plus pending to
   `unpaid_bookings`. If live values changed between the readings, investigate
   or repeat the comparison when they are stable.
8. Run the same `--dry-run` command a second time and compare its counts,
   sequence values, and anomalies with the first run. Investigate every
   unexpected change; a pending booking may become expired after its 15-minute
   hold, while the combined pending and expired count should remain stable.
9. Recheck the Purchase table counts from step 5. They must remain zero except
   for migration records. Record sanitized results, revision, counts, sequence
   values, anomaly status, and UTC times. Do not include tokens, database URLs,
   personal information, or other secrets.

The new database must remain empty until PUR-025. Do not use `--apply`,
`--verify`, `--repair`, any reset or seed setting, or a write-capable Spacey
database login during these checks.
