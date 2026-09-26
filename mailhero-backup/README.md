# Mail Hero independent backup task

This directory installs a Python/GPG/systemd collector, separate from Compose and the existing offen volume-backup service. It does not run the Mail Hero application on this server. The application remains a Cloudflare Worker. Do not mount these directories into the existing `./data` or private `env` backup trees: that would recursively back up the collector's own snapshots.

| Purpose | Server path |
| --- | --- |
| Reviewed public Mail Hero source, detached at an exact commit | `/home/xiziyi/mail-hero-backup-code` |
| Private staging, encrypted archives and local receipts | `/home/xiziyi/mail-hero-backup` |
| Dedicated machine credentials (xiziyi, mode 0600) | `/home/xiziyi/.config/mail-hero-backup/credentials.env` |
| Armored public key only | `/home/xiziyi/.config/mail-hero-backup/recovery-public.asc` |
| Already public-key-encrypted application-key escrow | `/home/xiziyi/.config/mail-hero-backup/credential-key.gpg` |
| Deployment's exact Mail Hero commit (public metadata) | `/home/xiziyi/.config/mail-hero-backup/source-commit` |

The collector uses only `BACKUP_TOKEN`, independent `BACKUP_RECEIPT_KEY`, and an Access service token restricted to the backup API. It needs no Cloudflare administrator token, Wrangler login, R2 S3 credential or recovery private key. Keep the private recovery key on the trusted recovery device. Prepare `credential-key.gpg` on that device from the existing application encryption key; never put the plaintext key on the server. The completed env file stays on the host and is never committed or pasted into chat.

## Prepare and install, with scheduling disabled

Confirm `/usr/bin/python3`, `/usr/bin/gpg`, Git and systemd are installed. Provision the mode-0700 config directory owned by `xiziyi`, the completed mode-0600 env file, the public key and encrypted escrow. Verify that `BACKUP_RECIPIENT` is the full 40- or 64-hex public-key fingerprint, and that the exact Mail Hero HTTPS origin and dedicated Access service policy are in place. This installer never logs into Cloudflare or modifies Access policies.

Use the **full pushed Mail Hero commit SHA** selected for this deployment. It is supplied at install time because recording this same repository's final commit inside that commit would be circular. The host stores the selected pin in `source-commit`; the service checks both Git HEAD and the collector file's cleanliness before every run.

```sh
sudo bash mailhero-backup/install.sh \
  --commit FULL_40_HEX_MAIL_HERO_COMMIT \
  --recipient FULL_VERIFIED_GPG_FINGERPRINT
sudo systemctl is-enabled mailhero-backup.timer
```

The expected timer state is **disabled**. The installer validates credentials' ownership and permissions without printing values, checks the public key fingerprint, refuses a dirty/unexpected checkout, fetches and checks out only the requested commit, and installs a root-owned source tree readable by `xiziyi`. It does not reset/clean an existing checkout or touch Compose. The service runs as `xiziyi` with read-only home/system access except the dedicated state directory; `/tmp` is private to the service.

Run the first collection under supervision:

```sh
sudo systemctl start mailhero-backup.service
sudo systemctl status mailhero-backup.service --no-pager
sudo journalctl -u mailhero-backup.service -n 30 --no-pager
```

Success means export, public-key encryption, remote upload, complete encrypted read-back checksum verification, and durable signed finish all succeeded. The private local output contains `latest-success.json` and encrypted snapshots. A new snapshot is not successful merely because an upload exists. The collector has a 30-minute lease limit; failures leave the preceding verified backup intact and do not automatically change plans or retention.

## Verify recovery, then explicitly enable

On the recovery device, use the same pinned release's `deploy/backup/mailhero_backup.py restore` command and its runbook. Supply the verified encrypted archive SHA-256 and the latest independent deletion journal. Restore only into a new isolated local directory first. Validate D1-compatible SQL, original R2 keys/metadata, stable event IDs and payload bytes, application-key escrow decryption, and deletion handling. The restored service must remain paused; no LLM/Todoist calls are part of a restore rehearsal. The pinned Mail Hero runbook describes the separate steps for new Cloudflare resources and DO state reconstruction.

After an actual successful isolated recovery rehearsal, enable scheduling explicitly:

```sh
sudo bash mailhero-backup/install.sh \
  --commit FULL_40_HEX_MAIL_HERO_COMMIT \
  --recipient FULL_VERIFIED_GPG_FINGERPRINT \
  --enable --restore-verified
sudo systemctl list-timers mailhero-backup.timer --no-pager
```

The timer starts at **04:17 UTC**, with up to 10 minutes jitter. The existing offen backup schedule has not been inspected; compare the real schedules before enabling and choose another time if they overlap. `Persistent=true` catches a missed run after host downtime. The collector uses an independent backup bucket and local encrypted copies, keeping 7 daily and 4 weekly snapshots after verified success; this changes neither existing Vultr backup retention nor its private credentials.

To stop scheduling without deleting backups:

```sh
sudo systemctl disable --now mailhero-backup.timer
```

Updating requires another explicit immutable Mail Hero SHA. Repeat the default install command to update the source with the timer disabled, perform the appropriate regression/recovery check, then enable explicitly. No command in this directory deploys the Mail Hero Worker, changes Todofy, imports a production database, or sends mail.

## Offline verification

```sh
python3 -m unittest discover -s mailhero-backup -p 'test_*.py' -v
bash -n mailhero-backup/install.sh mailhero-backup/run.sh
```

These checks validate the deployment guards and unit configuration without touching a host. They complement the Mail Hero repository's real SQLite/R2 fixture and GPG encryption/restore tests; they do not prove production credentials, daily scheduling or a real disaster recovery.
