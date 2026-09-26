#!/usr/bin/env bash
set -euo pipefail
umask 077

code=/home/xiziyi/mail-hero-backup-code
config=/home/xiziyi/.config/mail-hero-backup
state=/home/xiziyi/mail-hero-backup
expected=$(cat "$config/source-commit")
[[ "$expected" =~ ^[0-9a-f]{40}$ ]] || { echo 'mailhero-backup: invalid source pin' >&2; exit 1; }
actual=$(git -c safe.directory="$code" -C "$code" rev-parse HEAD)
[[ "$actual" == "$expected" ]] || { echo 'mailhero-backup: source revision differs from pin' >&2; exit 1; }
git -c safe.directory="$code" -C "$code" diff --quiet HEAD -- deploy/backup/mailhero_backup.py || {
  echo 'mailhero-backup: pinned collector has local changes' >&2; exit 1;
}
[[ "${BACKUP_RECIPIENT:-}" =~ ^([0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})$ ]] || {
  echo 'mailhero-backup: full recipient fingerprint required' >&2; exit 1;
}
[[ -n "${MAIL_HERO_ORIGIN:-}" ]] || { echo 'mailhero-backup: origin missing' >&2; exit 1; }
exec /usr/bin/python3 -I "$code/deploy/backup/mailhero_backup.py" collect \
  --origin "$MAIL_HERO_ORIGIN" --output "$state" \
  --public-key-file "$config/recovery-public.asc" --recipient "$BACKUP_RECIPIENT" \
  --credential-key-envelope "$config/credential-key.gpg" --lease-seconds 1800
