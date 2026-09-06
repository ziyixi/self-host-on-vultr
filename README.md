# self-host-on-vultr
This is the collection of my self-deployed servers on Vultr VM.

## Newsletter operations

The newsletter service and its external daily trigger are defined in
`docker-compose.yml`. They have no published ports and do not mount Docker's
socket. The service image comes from GHCR; the lightweight trigger is built
locally from the same pinned image's standalone client.

**A Compose checkout is not a complete migration.** Private env files, persistent
newsletter data, and a dedicated Codex login must be provisioned separately.
Start with [the bootstrap and migration runbook](newsletter/README.md), then run:

```sh
sh newsletter/bootstrap.sh check
```

That check is offline and read-only for mounted files; it does not refresh login
or send mail. Missing auth requires `sh newsletter/bootstrap.sh login` while both
newsletter services are stopped. No host installation of Codex or Python packages
is required.

The daily schedule starts at **07:00 America/Los_Angeles**, targeting delivery
before **09:30 local time**. The named zone automatically follows daylight saving
time (14:00 UTC in summer, 15:00 UTC in winter). Never enable it alongside the old
GitHub Actions schedule. The pinned service has a 90-minute content-workflow
budget; the external client waits up to two hours, nominally to 09:00, leaving
another 30 minutes before the target. This is a scheduling margin, not a guarantee
against provider outages or inbox delays. Updating runtime limits requires
checking the daily start time and trigger together.

`update.sh` remains the existing whole-stack update utility. It stops all services
and prunes images; use the targeted commands in the newsletter runbook when
preserving unrelated service uptime and rollback images matters.
