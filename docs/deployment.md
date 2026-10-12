# Deploying the Purchase service

Deployment is not yet enabled. In the current revision `GET /health` reports only
that the process runs and which revision it is; it does not open a database
connection, so a healthy check says nothing about the database. No migration,
data copy or traffic switch runs in this workflow (those are PUR-015, PUR-024 and
PUR-025).

## Service address and deployed revision

| | |
|---|---|
| Address | `https://purchase.cs403bkk26.space` (proposed, not yet live) |
| Health | `GET /health` → `{"status": "ok", "revision": "<git sha>"}` |
| Deployed revision | none yet |

Update this table after each verified release: source SHA, image digest,
workflow run URL and the time `/health` reported that SHA.

## Names

| Name | Value |
|---|---|
| `NS` (Nomad namespace) | `purchase` (proposed) |
| `JOB` (Nomad job) | `purchase` |
| `VAR` (runtime variable) | `nomad/jobs/purchase` (proposed) |
| `HOST` | `purchase.cs403bkk26.space` (proposed) |

The job file is [`deploy/purchase.nomad.hcl`](../deploy/purchase.nomad.hcl). Its
runtime variable lives under the job-owned path so the job's workload identity
can read it without broader variable permissions.

## Runtime variable items

| Item | Value |
|---|---|
| `database_url` | The Purchase database and its runtime login, never spacey's. Rendered into `DATABASE_URL`. |

The platform owner stores it. It must never be passed as a job argument,
committed, or copied into GitHub. A missing item fails the release instead of
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
- GitHub's `production` environment requires the designated release review.

Set `DEPLOY_ENABLED=true` as a repository variable only after setup and review.
In the protected `production` environment set variables `NOMAD_NAMESPACE`,
`APP_HOSTNAME` and `RUNTIME_VARIABLE` to the approved names, and secrets
`NOMAD_ADDR` and a fresh namespace-scoped `NOMAD_TOKEN`.

## Release

`main` is what gets released. After review and merge, the release owner runs the
`release` workflow from `main` (Actions → release → Run workflow). It runs the
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
