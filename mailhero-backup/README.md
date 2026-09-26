# Mail Hero backup with Docker Compose

`mailhero-backup` is an independent backup client in the main `docker-compose.yml`. Mail Hero itself remains on Cloudflare. The client uses a GitHub CI image pinned by immutable GHCR digest; Python, GPG and the scheduler are inside that image. Normal operation needs Docker Compose and the existing private directories, without a host source checkout, root service or systemd timer.

The client backs up only Mail Hero: D1 schema and records, stored email objects and frozen webhook payloads, coordinator recovery state, deletion records, and the encrypted application-key escrow. It does not mount the host filesystem, Docker socket, Todofy, Newsletter, or another service's database. Its two directories stay outside this stack's `./data` and `./env` backup roots, so the existing offen backup does not recursively collect these snapshots.

| Host path | Container path | Access |
| --- | --- | --- |
| `/home/xiziyi/.config/mail-hero-backup` | `/run/mailhero-backup` | Read only |
| `/home/xiziyi/mail-hero-backup` | `/var/lib/mailhero-backup` | Private staging, encrypted archives, receipts and scheduler state |

Both directories belong to `xiziyi` (`1000:1000`) and have mode `0700`. The configuration directory already contains:

- `credentials.env`, mode `0600`: `MAIL_HERO_ORIGIN`, verified full `BACKUP_RECIPIENT` fingerprint, `BACKUP_TOKEN`, independent `BACKUP_RECEIPT_KEY`, and the dedicated Access service-token pair.
- `recovery-public.asc`: the verified public key only.
- `credential-key.gpg`: the application encryption key already encrypted to that public key.

Reuse these files. The runtime reads the literal credentials file directly; Compose has no `env_file` for this service. Do not paste credentials into commands/chat, source the file, or copy secrets into Compose variables. No recovery private key, Cloudflare administrator token, Wrangler login or R2 S3 credential belongs in the container. The private recovery key stays on the trusted recovery device. Missing host directories fail instead of being silently created by Docker.

## First start

From this deployment repository on the server, check ownership and permissions without printing contents:

```sh
id -u xiziyi
id -g xiziyi
stat -c '%u:%g %a %n' \
  /home/xiziyi/.config/mail-hero-backup \
  /home/xiziyi/mail-hero-backup \
  /home/xiziyi/.config/mail-hero-backup/credentials.env
```

Expect UID/GID `1000:1000`, directories `700`, and credentials `600`. The pinned image must exist and the exact Mail Hero origin must already allow the dedicated Access service token for its backup API. Image publication and valid credentials are separate checks.

Pull only this service. Before enabling its scheduler, run one supervised collection:

```sh
docker compose pull mailhero-backup
docker compose run --rm --no-deps mailhero-backup once
```

Success means export, public-key encryption, remote upload, complete encrypted read-back checksum verification, and signed completion all succeeded. An uploaded archive alone is not a verified backup. The preceding verified backup remains available if a new run fails. The application snapshot lease is at most 30 minutes, and incoming mail continues to archive while a valid snapshot is collected.

Verify isolated recovery on the trusted recovery device using the matching Mail Hero release's `deploy/backup/mailhero_backup.py restore` command and runbook. Supply the verified archive SHA-256 and latest independent deletion journal. The rehearsal uses new isolated storage and keeps delivery paused; it must not call Todoist or an LLM. The encrypted application-key escrow needs the private recovery key on that device.

After the first verified collection and recovery check, start the scheduler:

```sh
docker compose up -d --no-deps mailhero-backup
docker compose ps mailhero-backup
docker compose exec -T mailhero-backup python3 -I /app/container.py health
docker compose logs --tail=30 mailhero-backup
```

These commands affect only Mail Hero backup. The health command reports runtime/scheduling status without displaying credentials or mail; it is not proof of a complete disaster-recovery exercise. Logs should contain safe status and error codes only. Do not use `docker inspect` to dump a running container's environment or print the private config files when diagnosing a failure.

## Schedule, retention and updates

The container's scheduler runs daily at **04:17 UTC**, configured by `BACKUP_AT_UTC` in Compose. Scheduler state and receipts survive container replacement in the state directory. The runtime prevents overlapping collections with a lock in the shared state directory. Its scheduler does not modify the existing offen backup service.

Retention keeps the latest verified snapshot for each of up to **7 distinct days and 4 distinct ISO weeks**, locally and in the independent backup bucket. Overlapping selections share one archive, so this retains at most 11 archives, not 11 guaranteed distinct copies. The latest successful receipt and deletion journal are part of the recovery procedure. Changing a container image does not delete these files.

For an update, review and replace only this service's image digest with the tested CI release. Then:

```sh
docker compose pull mailhero-backup
docker compose up -d --no-deps mailhero-backup
docker compose exec -T mailhero-backup python3 -I /app/container.py health
```

Compose gives the runtime 4 minutes (240 seconds) to cancel an active collection and release its snapshot lease on shutdown. A failed/interrupted collection is not marked successful; lease expiry also restores normal application processing. Do not run the whole-stack `update.sh` or an unscoped `docker compose down` for a Mail Hero backup update. No build or package installation occurs on the server.

To pause the daily scheduler without removing backups:

```sh
docker compose stop mailhero-backup
```

Resume with the scoped `up -d --no-deps` command above. For an additional manual run, use `docker compose run --rm --no-deps mailhero-backup once` directly. If a collection already holds the shared lock, the command reports that it is already running without starting a second collection. Do not start a second scheduler against the same state directory.

The former `install.sh`, systemd units and host `run.sh` are retired. Do not run an old timer alongside the Compose service. No systemd timer was present in the deployment preflight; if migrating another host that has one, stop its scheduling and let an active run finish before starting Compose.

## Offline deployment checks

```sh
python3 -B -m unittest discover -s tests -p 'test_mailhero_backup_deployment.py' -v
```

The contract checks use Compose's configuration parser with private env/path resolution disabled. They require Compose 5.1.0 or newer and contact no Docker daemon, Cloudflare API or mail service. They verify the service's mounts, isolation, resource limits and runtime commands. The Mail Hero repository separately tests the image, scheduler, synthetic export/encryption and isolated restore. None of these local checks proves live credentials or that a daily production backup has succeeded.
