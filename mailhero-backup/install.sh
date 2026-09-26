#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  echo 'Usage: sudo bash mailhero-backup/install.sh --commit FULL_MAIL_HERO_SHA --recipient FULL_GPG_FINGERPRINT [--enable --restore-verified]'
  echo 'Without --enable the timer is installed disabled. A prior isolated restore is required to enable it.'
}
commit='' recipient='' enable=false restored=false
while (($#)); do
  case "$1" in
    --commit) (($# >= 2)) || { usage; exit 2; }; commit=$2; shift 2 ;;
    --recipient) (($# >= 2)) || { usage; exit 2; }; recipient=$(printf '%s' "$2" | tr '[:lower:]' '[:upper:]'); shift 2 ;;
    --enable) enable=true; shift ;;
    --restore-verified) restored=true; shift ;;
    --help|-h) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || { echo 'A full immutable Mail Hero commit SHA is required.' >&2; exit 2; }
[[ "$recipient" =~ ^([0-9A-F]{40}|[0-9A-F]{64})$ ]] || { echo 'A full GPG fingerprint is required.' >&2; exit 2; }
[[ "$enable" == false || "$restored" == true ]] || { echo '--enable requires --restore-verified after an isolated recovery rehearsal.' >&2; exit 2; }
[[ "$EUID" == 0 ]] || { echo 'Run this installer with sudo.' >&2; exit 1; }
for command in git gpg python3 systemctl systemd-analyze install stat; do
  command -v "$command" >/dev/null || { echo "Required host command missing: $command" >&2; exit 1; }
done

source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
config=/home/xiziyi/.config/mail-hero-backup
code=/home/xiziyi/mail-hero-backup-code
state=/home/xiziyi/mail-hero-backup
account_uid=$(id -u xiziyi)
[[ -d "$config" && ! -L "$config" && "$(stat -c %a "$config")" == 700 ]] || { echo 'Create a private mode-0700 Mail Hero backup config directory first.' >&2; exit 1; }
for name in credentials.env recovery-public.asc credential-key.gpg; do
  [[ -f "$config/$name" && ! -L "$config/$name" ]] || { echo "Missing regular backup configuration file: $name" >&2; exit 1; }
done
[[ "$(stat -c %a "$config/credentials.env")" == 600 && "$(stat -c %u "$config/credentials.env")" == "$account_uid" ]] || {
  echo 'credentials.env must be owned by xiziyi and have mode 0600.' >&2; exit 1;
}
[[ "$(stat -c %u "$config")" == "$account_uid" ]] || { echo 'The backup config directory must be owned by xiziyi.' >&2; exit 1; }

# Parse as data, never source an environment file as a shell script. Do not
# print values; the dedicated secrets never need to enter terminal history.
python3 - "$config/credentials.env" "$recipient" <<'PY'
import re, sys, urllib.parse
values = {}
for line in open(sys.argv[1], encoding='utf-8'):
    line = line.strip()
    if not line or line.startswith('#'):
        continue
    if '=' not in line:
        raise SystemExit('Invalid backup environment file format.')
    key, value = line.split('=', 1)
    if key in values:
        raise SystemExit('Duplicate backup environment setting.')
    values[key] = value.strip().strip('"').strip("'")
required = ('BACKUP_TOKEN', 'BACKUP_RECEIPT_KEY', 'CF_ACCESS_CLIENT_ID', 'CF_ACCESS_CLIENT_SECRET', 'MAIL_HERO_ORIGIN', 'BACKUP_RECIPIENT')
if any(not values.get(key) for key in required) or len(values['BACKUP_TOKEN']) < 32 or not re.fullmatch('[0-9a-fA-F]{64}', values['BACKUP_RECEIPT_KEY']):
    raise SystemExit('Dedicated backup credentials are missing or invalid.')
origin = urllib.parse.urlsplit(values['MAIL_HERO_ORIGIN'])
if origin.scheme != 'https' or not origin.hostname or origin.username or origin.password or origin.path not in ('', '/') or origin.query or origin.fragment:
    raise SystemExit('An exact HTTPS Mail Hero origin is required.')
if values['BACKUP_RECIPIENT'].upper() != sys.argv[2]:
    raise SystemExit('Backup recipient differs from the reviewed fingerprint.')
PY

keyring=$(mktemp -d)
trap 'rm -rf -- "$keyring"' EXIT
[[ "$(head -n 1 "$config/recovery-public.asc")" == '-----BEGIN PGP PUBLIC KEY BLOCK-----' ]] || { echo 'An armored public-key export is required.' >&2; exit 1; }
listing=$(gpg --batch --no-tty --homedir "$keyring" --with-colons --show-keys "$config/recovery-public.asc" 2>/dev/null) || { echo 'Cannot inspect recovery public key.' >&2; exit 1; }
printf '%s\n' "$listing" | awk -F: -v expected="$recipient" '$1=="fpr" && $10==expected { found=1 } END { exit !found }' || { echo 'Recovery public key fingerprint does not match.' >&2; exit 1; }
if printf '%s\n' "$listing" | awk -F: '$1=="sec" { found=1 } END { exit !found }'; then
  echo 'Private keys must not be installed on the collector host.' >&2; exit 1
fi

source "$source_dir/upgrade-guard.sh"
prepare_mailhero_backup_upgrade

# Refuse drift before changing an existing checkout. No reset/clean operation
# is used, and this path is separate from the existing Compose backup tree.
if [[ -e "$code" ]]; then
  [[ -d "$code/.git" && ! -L "$code" ]] || { echo 'Existing source path is not the expected Git checkout.' >&2; exit 1; }
  [[ "$(git -c safe.directory="$code" -C "$code" remote get-url origin)" == 'https://github.com/ziyixi/mail-hero.git' ]] || { echo 'Unexpected source repository origin.' >&2; exit 1; }
  [[ -z "$(git -c safe.directory="$code" -C "$code" status --porcelain)" ]] || { echo 'Source checkout has local changes; preserve them before updating.' >&2; exit 1; }
else
  git clone --no-checkout https://github.com/ziyixi/mail-hero.git "$code"
fi
git -c safe.directory="$code" -C "$code" fetch --depth=1 origin "$commit"
git -c safe.directory="$code" -C "$code" checkout --detach "$commit"
[[ "$(git -c safe.directory="$code" -C "$code" rev-parse HEAD)" == "$commit" && -f "$code/deploy/backup/mailhero_backup.py" ]] || { echo 'Pinned collector revision is unavailable.' >&2; exit 1; }
chown -R root:root "$code"
chmod -R u=rwX,go=rX "$code"
[[ ! -e "$state" || ( -d "$state" && ! -L "$state" ) ]] || { echo 'Backup state path must be a regular directory.' >&2; exit 1; }
install -d -m 700 -o xiziyi -g xiziyi "$state"
pin_file=$(mktemp "$config/.source-commit-XXXXXX")
printf '%s\n' "$commit" > "$pin_file"
mv -T "$pin_file" "$config/source-commit"
chown root:root "$config/source-commit"
chmod 444 "$config/source-commit"
install -D -m 755 -o root -g root "$source_dir/run.sh" /usr/local/libexec/mailhero-backup-run
install -m 644 -o root -g root "$source_dir/mailhero-backup.service" /etc/systemd/system/mailhero-backup.service
install -m 644 -o root -g root "$source_dir/mailhero-backup.timer" /etc/systemd/system/mailhero-backup.timer
systemd-analyze verify /etc/systemd/system/mailhero-backup.service /etc/systemd/system/mailhero-backup.timer
systemctl daemon-reload
if [[ "$enable" == true ]]; then
  systemctl enable --now mailhero-backup.timer
  echo 'Mail Hero backup timer enabled after explicit restore acknowledgement.'
else
  systemctl disable --now mailhero-backup.timer
  echo 'Mail Hero backup installed; timer is disabled. Run one supervised backup and recovery rehearsal before enabling.'
fi
