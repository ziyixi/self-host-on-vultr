# Newsletter: bootstrap, operation, and migration

## State that is deliberately not in the image or Git

| State | Location | Handling |
| --- | --- | --- |
| Service secrets | `env/newsletter.env` | Operator-owned, mode 600; raw `KEY=value` lines |
| Trigger capabilities | `env/newsletter-trigger.env` | Mode 600; only matching editor/send tokens |
| Live SQLite/artifacts | `data/newsletter/` | UID/GID 10001, mode 700; never reuse a fixture database |
| Codex login and refreshed runtime cache | `/home/xiziyi/.local/share/newsletter/codex-auth/` | UID/GID 10001, directory 700, `auth.json` 600 |
| Tested service/client version | `x-newsletter-image` in `docker-compose.yml` | Immutable GHCR SHA-256 digest |

The existing backup container reads `env/` and `data/`. Codex auth intentionally
lives outside both: keep a separately protected backup or log in again after a
migration. Never put auth in Git, an image layer, public CI, command arguments,
support logs, or chat. Do not copy the whole interactive `~/.codex` directory.

## Fresh Linux/amd64 host

1. Install Docker Engine and Compose **5.1.0 or newer**. CI explicitly installs
   5.1.0, matching the production host, rather than relying on the runner's
   preinstalled version. Compose 2.38.2 can still resolve service env files with
   `config --no-env-resolution`, so it is not supported for these secret-free
   checks. Do not add fake env files to work around an old parser. The bootstrap helper uses
   only POSIX shell/core Linux utilities and Docker on the host; checks and login
   use the runtime already packaged inside the pinned newsletter image.
2. Check out the deployment repository at `/home/xiziyi/self-host-on-vultr`.
   Preserve existing `env/`, `data/`, and local modifications. Use `git pull
   --ff-only` only after inspecting the diff/status; never `reset --hard` or
   `git clean` to make a deployment work. On a replacement host, also restore
   the existing stack's other private env files (for example Todofy and backup):
   Compose validates those paths even when selecting only newsletter. Do not
   create fake credentials to silence a missing-file error.
3. Create NEW private env files from their examples, with mode 600. Do not replace
   existing files. Use raw values, without shell quotes or interpolation. Add
   Notion/Todofy/Resend configuration and three distinct random service tokens of
   at least 24 characters. The trigger receives only the matching editor/send
   tokens; bootstrap rejects any other non-comment line in its private env file
   without echoing the offending key or value. Check the final recipient and
   sender yourself. `onboarding@resend.dev`
   can send only to the Resend account's registered email.
4. Provision only the two dedicated directories. On a genuinely fresh host,
   after verifying that these are the intended paths:

   ```sh
   sudo install -d -m 700 -o 10001 -g 10001 /home/xiziyi/self-host-on-vultr/data/newsletter
   sudo install -d -m 700 -o 10001 -g 10001 /home/xiziyi/.local/share/newsletter/codex-auth
   ```

   For restored/existing data, inspect first and repair ownership only within
   those dedicated paths. Never change ownership of all `data/`, `env/`, or the
   personal home directory. Existing `auth.json` must be UID/GID 10001, mode 600.
5. Confirm `x-newsletter-image` is the tested immutable GHCR digest, then pull
   **only** newsletter. Public GHCR images require no registry login:

   ```sh
   docker compose pull newsletter
   ```

6. Complete the dedicated login if no valid one was restored:

   ```sh
   docker compose stop newsletter-trigger newsletter
   sh newsletter/bootstrap.sh login
   ```

   Open the official login URL shown in your own terminal and complete device
   authentication. The helper refuses while a newsletter/trigger container is
   running, uses the bundled Codex CLI with explicit file-backed ChatGPT auth,
   and mounts only the dedicated auth directory read-write. It never receives
   the service's Notion, Todofy, or Resend keys. No host Codex install or port
   publication is needed. It does not silently replace auth with an older copy.
7. Run `sh newsletter/bootstrap.sh check`. Mounted auth/data are read-only and
   the check containers have no network; this checks permissions, SDK presence,
   configuration and matching trigger capabilities, not credential validity.
   It neither refreshes login nor opens SQLite. Errors explain what to repair.
8. Start only the service and let its real startup preflight finish:

   ```sh
   docker compose up -d --no-deps newsletter
   docker compose ps newsletter
   docker compose logs --tail 80 newsletter
   docker compose build newsletter-trigger
   ```

   Unlike the offline bootstrap check, real startup may refresh login, contact
   dependencies and initialize persistent storage. The service must become
   healthy before scheduling. Keep the scheduler stopped until a real content
   run and the separately authorized test email have been verified.
9. Disable the legacy GitHub Actions daily workflow and ensure no old send run
   remains active. Then enable the new daily trigger:

   ```sh
   docker compose up -d --no-deps newsletter-trigger
   docker compose ps newsletter newsletter-trigger
   docker compose logs --tail 40 newsletter-trigger
   ```

## Scheduling and delivery

`newsletter-trigger/crontab` runs at `0 15 * * *` with `TZ=UTC`, matching the old
workflow's scheduled time (not GitHub's occasionally delayed execution time).
Issue dates use `America/Los_Angeles`. Content collection starts at that time;
email follows after generation, validation and rendering complete.

Compose explicitly selects `NEWSLETTER_WORKFLOW=dag` and a 5400-second
(90-minute) content-workflow deadline. The versioned recipe and discovery
instructions are packaged in the pinned newsletter image; no external routine
or in-service cron is needed. Changes to those packaged files require a tested
newsletter image release, not edits inside a running container.

The service owns the complete editorial sequence, including one automatic
minimal revision and a fresh-session final review after an initial HOLD. Passing
the initial review skips both additional calls. An unresolved second HOLD stops
publication; neither the cron client nor an operator log viewer grants a factual
waiver. Removing weak claims and producing a shorter issue is preferable to
infinite retries. Inspect `workflow.continuations` when an older unsent HOLD is
continued through the separately frozen upgrade repair graph; the original
deadline, research, failed edition and usage remain intact. New daily graphs
include this sequence directly. Manual monitoring is not an execution step.

The client waits up to 7200 seconds (two hours) and uses a stable
`daily-YYYY-MM-DD` run key. The additional 30 minutes leave room for Notion
confirmation of adopted material, private Todofy enrichment, rendering and
delivery. The timeout is an upper bound, not a promise that failed providers
will succeed; if changing workflow limits, keep the cron command, trigger
startup check and CI smoke check aligned and preserve this margin.
The service additionally guards sending with the frozen render hash and a stable
send idempotency key. An interrupted/ambiguous send must be inspected rather than
retried with a new key. Restarting the scheduler does not backfill missed days.
For the authorized test, use that issue date's normal `daily-YYYY-MM-DD` key so
the scheduled run reuses the same result/receipt instead of attempting a second
delivery. Preserve the original issue date and request key when inspecting or
resuming a run across midnight. The service permits only one send attempt per
issue date: a new key is not a way to request a same-date resend. Never create a
new empty database to bypass that protection. Do not run test sends casually or
start two schedulers.

The trigger receives only editor/send capabilities. It has no direct provider
credentials, state mounts, published ports, or Docker socket. Its Dockerfile
copies only the standalone stdlib trigger client from the pinned service image
and installs checksum-verified Supercronic. Do not add `-overlapping`: scheduled
executions should remain serialized.

Provider acceptance is not proof of Gmail inbox delivery. Check the recipient's
inbox or authorized provider delivery status before calling a test successful.

## Authentication recovery

Container packaging makes the runtime reproducible, **not the account login**.
Account auth can expire or be revoked. Preserve the latest refreshed auth state
between runs; do not overwrite it at every deploy. Keep one dedicated login for
this serialized service rather than sharing it with concurrent interactive jobs.
These constraints follow the [official authentication guidance](https://learn.chatgpt.com/docs/auth)
and [persistent account-auth guidance](https://learn.chatgpt.com/docs/auth/ci-cd-auth).

If startup or a job reports authentication failure:

1. Stop `newsletter-trigger` and `newsletter`; inspect the failure category, not
   token contents. Do not keep relaunching the same failing job automatically.
2. Run `sh newsletter/bootstrap.sh login` from the deployment checkout and finish
   device login privately. Existing login state is never deleted by the helper.
3. Run `sh newsletter/bootstrap.sh check`, then start only newsletter and verify
   health before restarting the trigger. Never resume both old and new hosts.

The bootstrap script does not auto-fix permissions, fetch images, or restart
services. Every persistent change remains an explicit operator step.

## Upgrade and rollback

Wait for newsletter CI to test and publish its Linux/amd64 image. Update the
versioned `x-newsletter-image` digest; the service and cron client must move
together. Inspect the deployment Git diff and preserve private configuration.

```sh
docker compose stop newsletter-trigger
docker compose pull newsletter
docker compose build newsletter-trigger
sh newsletter/bootstrap.sh check
docker compose up -d --no-deps newsletter
# Wait for healthy, inspect the release, then:
docker compose up -d --no-deps newsletter-trigger
```

Keep the previous digest and a consistent data backup. Avoid pruning old images
until validation finishes. A code rollback may also require the corresponding
database backup if a future release changes its schema. Do not change a live
database's configured delivery backend/recipient or reuse mock storage.

## Migration checklist: do not forget login

- Record the deployed commit/image digest and inspect pending jobs/sends.
- Stop the old trigger, then the old newsletter. Keep both stopped through the
  transfer so no SQLite writer or auth refresh races with the snapshot.
- Take a consistent snapshot of the **entire** `data/newsletter/` directory,
  including SQLite WAL/SHM files if present. A plain copy of a live database is
  not a safe backup; the existing generic backup mount alone does not establish
  SQLite consistency.
- Transfer the two private env files and data over a trusted channel. Transfer
  the latest dedicated `auth.json` separately into an otherwise fresh auth
  directory, or perform a fresh dedicated login on the destination. Do not copy
  login-generated `tmp/` helper links, logs, personal `config.toml`, or host CLI
  binaries: the pinned container recreates its own runtime cache. Do not restore
  stale auth from an image or original seed.
- Preserve secret file modes and restore UID/GID 10001 on the two dedicated
  container-writable directories. Keep service/trigger token pairs identical.
- Restore the same immutable image digest first. Run bootstrap check, start only
  newsletter, and verify real readiness before enabling exactly one scheduler.
- Do not run old and new hosts against the same login/database concurrently.
  Retain a private rollback copy until content and delivery have been checked.
