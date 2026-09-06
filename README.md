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

The daily schedule preserves the previous newsletter workflow: **15:00 UTC**
(08:00 PDT / 07:00 PST). Never enable it alongside the old GitHub Actions schedule.

`update.sh` remains the existing whole-stack update utility. It stops all services
and prunes images; use the targeted commands in the newsletter runbook when
preserving unrelated service uptime and rollback images matters.
