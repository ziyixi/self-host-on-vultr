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
8. Authorize both independent Notion databases and initialize their managed
   columns using the [dual-database setup](#notion-materials-and-edition-archive)
   below. `update.sh` and startup never create these columns implicitly. This
   uses the existing private Notion token, not the dedicated Codex login.
9. Start only the service and let its real startup preflight finish:

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
10. Disable the legacy GitHub Actions daily workflow and ensure no old send run
   remains active. Then enable the new daily trigger:

   ```sh
   docker compose up -d --no-deps newsletter-trigger
   docker compose ps newsletter newsletter-trigger
   docker compose logs --tail 40 newsletter-trigger
   ```

## Notion materials and edition archive

Use two independent Notion databases: one row per public paper/event in the
materials library, and one row per frozen edition in the daily archive. Two
linked views of one data source are not two databases. Share both with the same
Internal connection and enable Read content, Insert content and Update content.
Relations need access to both destinations. Keep the default title column;
the setup command adds the managed metadata columns, so do not build them by hand.

In the existing private `env/newsletter.env`, preserve all current secrets and
add both `NOTION_MATERIALS_DATA_SOURCE_ID` and `NOTION_EDITIONS_DATA_SOURCE_ID`.
Use **data source IDs**, not the page/database IDs from arbitrary links. Both
must be set, valid and different. Continue using `NEWSLETTER_NOTION=notion` and
the existing `NOTION_TOKEN`. Do not send tokens or passwords in chat or add
them to commands, Git, image layers or diagnostic output.

`NEWSLETTER_NOTION_ARCHIVE_PRIVATE=false` is the safe default. Set it to `true`
only after the owner explicitly authorizes storing the frozen Todofy events in
the archive database. The materials library never receives these events. Do
not Publish or publicly share the archive; inspect inherited page permissions
as well. Hiding columns is not an access-control boundary. Notion access does
not replace the dedicated Codex login: follow `bootstrap.sh login` when moving
to a host without a restored service login.

The two new IDs select the dual-database adapter. The old
`NOTION_DATA_SOURCE_ID` may remain in an existing private env file but is ignored
in dual mode; leaving both new IDs unset retains legacy single-database mode.
There is no fallback write to the old database if dual sync fails. Dual mode
requires the packaged daily DAG or a custom DAG with a supported `publish` tail,
not legacy projection barriers.

After selecting and pulling the tested image that contains `notion_cli`, inspect
both schemas before changing anything:

```sh
docker compose run --rm --no-deps --entrypoint python newsletter -m newsletter.notion_cli setup
```

If the targets and missing-column report are correct, explicitly initialize them:

```sh
docker compose run --rm --no-deps --entrypoint python newsletter -m newsletter.notion_cli setup --apply
```

These one-off containers inherit the service's data/auth mounts and private env,
but `setup` only calls the Notion HTTP/schema adapter: it does not open the
application Store, run a model, refresh Codex auth, collect content or send mail.
The existing service need not be stopped merely to initialize the Notion schema;
`--no-deps` does not start the scheduler or other services. Do not run a second
full newsletter server against the same mounted data. A bootstrap directory or
env-file error still needs to be repaired before Compose can launch the tool.

Without `--apply`, setup is read-only. With it, both schemas are checked first,
then missing managed columns are added; wrong field types or relation targets
are rejected, not destructively converted. This is not an atomic cross-database
migration. After an interrupted setup, inspect again before applying remaining
changes. Neither `update.sh` nor normal service startup implicitly creates
columns. Startup checks existing schemas and rejects permanent configuration or
authorization errors; temporary Notion outages are reported as degraded.

Once the tested service is healthy with the new configuration, its independent
background task backfills existing public materials and ready editions. It
does not add cron jobs, use LLM tokens, start new content runs or send test mail.
SQLite remains authoritative; Notion availability does not block normal email.
Historical test editions remain marked as tests, not rewritten or deleted.
The archive includes original frozen PNG charts and, if authorized, Todofy
events after the public content. Provider acceptance appears as submitted, not
as proof of inbox delivery.

Read synchronization counts and safe diagnostics while the service is running:

```sh
docker compose exec -T newsletter python -m newsletter.notion_cli status
```

`status` opens SQLite read-only and makes no provider calls. Check that bodies,
graphs and relations are actually present in Notion, not only that pages exist.
Unknown create/append outcomes are reconciled by reading before any retry;
never delete receipts to force another write. Do not hand-edit generated page
bodies; keep extra notes on a separate linked page. Changing destinations or
privacy policy requires explicit migration, not removing the destination guard.

Only after new-database verification may an operator archive the exact old
test pages into Notion trash. Do not delete SQLite packets, runs, frozen
editions, projection history or send receipts. Keep recoverable backups and
never restore a pre-send database to redo an archive. Full field definitions
and behavior are in the service's
[Notion guide](https://github.com/ziyixi/newsletter/blob/main/docs/notion.md).

## Scheduling and delivery

`newsletter-trigger/crontab` runs at `0 7 * * *` with
`TZ=America/Los_Angeles`, targeting delivery before **09:30 Los Angeles time**.
Issue dates use that same named zone. Content preparation starts at **07:00**;
email follows after generation, validation and rendering complete. The UTC start
is 14:00 during PDT and 15:00 during PST, including automatic seasonal changes;
do not replace the named zone with a fixed UTC offset. 07:00 is outside the
repeated or skipped early-morning hour on daylight-saving transition days.

[Supercronic uses the process `TZ` for scheduling](https://github.com/aptible/supercronic#timezone).
The trigger image already installs `tzdata` and now checks the Los Angeles zone
file at build time; both the image default and Compose set the same `TZ`.
`NEWSLETTER_TIME_ZONE` controls the client's issue date, not the cron scheduler,
so both settings must remain aligned. The crontab has no `CRON_TZ` override.

Compose explicitly selects `NEWSLETTER_WORKFLOW=dag` and a 5400-second
(90-minute) content-workflow deadline. The versioned recipe and discovery
instructions are packaged in the pinned newsletter image; no external routine
or in-service cron is needed. Changes to those packaged files require a tested
newsletter image release, not edits inside a running container.

The service owns a topic-first publication DAG. All selected topics receive a
researched brief and independent review before selected topics are deepened.
Approved complete versions are immediately checkpointed in SQLite. A chart or
reading-card problem does not veto its independent body or another topic; a body
may have one targeted rewrite/review, not a whole-issue HOLD loop. On research
deadline, fatal provider failure or interruption, the local tail can publish
already approved unaffected versions without another model call. It cannot
publish unverified content when nothing passed. A later confirmed factual error
can retract only the precise affected version with an independent evidence
receipt. The run/edition `publication` field records every topic's disposition;
unfinished topics remain follow-up leads rather than silently disappearing.

New publications bind `projection_required=False`: Notion is an asynchronous
one-way mirror, not a delivery dependency. Local evidence, review hashes,
recipient checks and send receipts remain mandatory. Unknown Notion writes are
not blindly repeated. Old frozen runs, review artifacts, projection gates and
send records retain their original policies; upgrading never retroactively
approves an old held draft. Manual monitoring is not an execution step.

The client waits up to 7200 seconds (two hours) and uses a stable
`daily-YYYY-MM-DD` run key. From an on-time 07:00 start, the 90-minute content
budget reaches 08:30, leaving 30 minutes inside the client wait for local
publication, private Todofy enrichment, rendering and delivery. The client's
nominal timeout is 09:00, with another 30 minutes before the 09:30 target for
operational and provider-to-inbox delay. This extra margin does not extend either
timeout or authorize another attempt. Provider acceptance is not an inbox-delivery
guarantee; outages, a busy service, failed research or delayed email can still miss
the target. If changing workflow limits, keep the cron command, trigger startup
check and CI smoke check aligned, and check start + client timeout < 09:30.
The service additionally guards sending with the frozen render hash and a stable
send idempotency key. An interrupted/ambiguous send must be inspected rather than
retried with a new key. Restarting the scheduler does not backfill missed days.
For an ordinary authorized test, use that issue date's normal `daily-YYYY-MM-DD` key so
the scheduled run reuses the same result/receipt instead of attempting a second
delivery. Preserve the original issue date and request key when inspecting or
resuming a run across midnight. The service permits only one send attempt per
issue date: a new key is not a way to request a same-date resend. Never create a
new empty database to bypass that protection. Do not run test sends casually or
start two schedulers.

After a confirmed normal delivery, an explicitly requested corrected preview can
use `POST /v1/editions/{id}/send-verification` with the send capability, its exact
frozen render hash and a stable request key. Without an extra approval this is
limited to one verification per date. If the user expressly requests another
new version after that verification was accepted, also supply
`X-Newsletter-Verification-After: <latest accepted verification edition UUID>`.
This approves only one successor to that receipt: stale predecessors, competing
children and unconfirmed/ambiguous deliveries are rejected. Repeating an existing
approval never makes another provider call. The normal cron client never supplies
this header or uses the verification route. Do not change the issue date, delete
receipts or send directly through Resend to bypass these checks.

If an operator explicitly requests a fresh test of a replaced architecture after
an unsent, terminal old run, first prove there has been **no send attempt for that
issue date**. An explicitly named test run may then freeze the new recipe while
preserving the old run and deadline. Never change a key merely to bypass an
ambiguous request or prior send; the date-level send guard still applies. The old
daily key remains an honest terminal receipt rather than being rewritten to look
like the new test. The next date uses the normal scheduled key and new recipe.

The trigger receives only editor/send capabilities. It has no direct provider
credentials, state mounts, published ports, or Docker socket. Its Dockerfile
copies only the standalone stdlib trigger client from the pinned service image
and installs checksum-verified Supercronic. Do not add `-overlapping`: scheduled
executions should remain serialized.

Schedule changes are baked into the trigger image: rebuild and recreate only
`newsletter-trigger` after the deployment checks pass. Do not merely restart the
old container or run a manual send to test the clock. For an offline schedule
inspection after building, `docker compose run --rm --no-deps --entrypoint
supercronic newsletter-trigger -debug -test /app/crontab` validates the configured
schedule syntax without executing the job. It does not print the next run time.
Keep exactly one scheduler enabled; a missed
07:00 run is not backfilled automatically, and changing the schedule must not
reset a run or send receipt. A schedule-only deployment does not require a test
email.

Provider acceptance is not proof of Gmail inbox delivery. Check the recipient's
inbox or authorized provider delivery status before calling a test successful.

## Source-first editorial configuration

The pinned service includes an editable public AI source guide at
`instructions/discovery/_sources/ai-ml.md`, alongside the eight discovery directions.
The guide is frozen with each accepted run, not fetched from mutable local files
mid-run. If mounting a custom discovery directory, include that optional nested
file to customize the guide; old directories without it keep their old behavior.
It is not a domain allowlist or proof that a paper is important. Candidate author,
institution, venue, contribution and consulted URLs stay unverified leads until
story research. The source-first update adds no worker, model review round,
scheduler, exposed port or change to the existing delivery approval chain.

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
# Wait for healthy. Explicit real schema check (uses a small model allowance):
docker compose exec -T newsletter python -m newsletter.schema_smoke --allow-model-calls
# Inspect a real frozen preview and separately authorized delivery, then:
docker compose up -d --no-deps newsletter-trigger
```

The offline CI/startup smoke cannot prove live schema acceptance. The opt-in
command above checks the production writer/reviewer schemas against the configured
model using only empty diagnostic envelopes; it does not access the service DB,
Notion, Todofy or Resend, and never creates an issue. Runtime startup also checks
all schema variants locally before spending tokens on discovery. See the service's
[provider acceptance guide](https://github.com/ziyixi/newsletter/blob/main/docs/provider-acceptance.md).

After a fixed shared writer-startup failure, an editor-authorized
`POST /v1/runs/{parent_id}/retry-stories` can create exactly one child run using
the same issue date and a fixed request key. It verifies and reuses the successful
upstream artifact hashes, then performs fresh writing and independent review.
It preserves the terminal parent and includes both runs' token usage. It cannot
retry a child, an ambiguous model execution, a factual rejection or a sent issue;
it does not send mail. This is an explicit post-fix recovery action, not a cron
retry loop. Never reset attempt rows or rerun discovery just to change a receipt.

Keep the previous digest and a consistent data backup. Avoid pruning old images
until validation finishes. A code rollback may also require the corresponding
database backup if a future release changes its schema. Do not change a live
database's configured delivery backend/recipient or reuse mock storage.

The prompt-promotion release migrates the verification ledger transactionally
from one row per date to a uniquely linked sequence, preserving all old rows.
Back up the stopped service's complete data directory before this upgrade. After
a new verification is attempted, never restore a pre-send backup or downgrade to
code that assumes one verification row per date: doing so would discard or
misinterpret a delivery receipt. Keep the current ledger and roll forward if a
subsequent fix is needed; sender-side idempotency is not a replacement for it.

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
- Preserve both Notion data source IDs and the explicit private-archive policy.
  Reauthorize both databases if using a new connection; initialize missing
  schemas explicitly with `notion_cli setup --apply`, never by deleting SQLite.
- Restore the same immutable image digest first. Run bootstrap check, start only
  newsletter, and verify real readiness before enabling exactly one scheduler.
- Do not run old and new hosts against the same login/database concurrently.
  Retain a private rollback copy until content and delivery have been checked.
