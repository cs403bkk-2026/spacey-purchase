<!--
Title = the commit on main: `<type>(purchase): <what it does> (PUR-NNN)`, e.g. `feat(purchase): add the create-booking endpoint (PUR-003)`.
Breaking API change: `feat(purchase)!: …` and a `BREAKING CHANGE: …` line below. See CONTRIBUTING.md.
-->

Refs #<!-- issue; use "Closes #N" on the ticket's last PR -->

## What and why

## How I checked it

<!-- The commands you ran and their output. `uv run pytest` counts on main and on this branch. -->

## Checklist

- [ ] Tests cover every new rule, default and error branch
- [ ] `openapi.yaml` is unchanged, or updated here and the change is described above
- [ ] No migration, or it is a new numbered file and never edits a merged one
- [ ] No other service is called; flows that depend on one are tested with each of its possible answers
- [ ] If the module layout changed, the matching `spacey` PR is linked

## Not checked / risks

<!-- What you did not verify, and how to roll back if a revert is not enough. -->
