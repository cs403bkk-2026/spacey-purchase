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
