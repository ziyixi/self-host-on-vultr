#!/usr/bin/env bash

# Sourced by install.sh after preflight validation and before changing source.
# Stop scheduling first, then refuse any backup that has not finished naturally.
prepare_mailhero_backup_upgrade() {
  local timer_load service_state
  timer_load=$(systemctl show --property=LoadState --value mailhero-backup.timer) || {
    echo 'Cannot determine backup timer state; no source changes were made.' >&2
    return 1
  }
  case "$timer_load" in
    not-found) ;; # Initial installation has no timer to disable.
    loaded|masked)
      systemctl disable --now mailhero-backup.timer || {
        echo 'Cannot disable backup scheduling; no source changes were made.' >&2
        return 1
      }
      ;;
    *)
      echo 'Unexpected backup timer state; no source changes were made.' >&2
      return 1
      ;;
  esac
  service_state=$(systemctl show --property=ActiveState --value mailhero-backup.service) || {
    echo 'Cannot determine backup service state; keep scheduling disabled and retry after checking it.' >&2
    return 1
  }
  case "$service_state" in
    inactive|failed) return 0 ;;
    *)
      echo 'A backup service is still active or transitioning. Scheduling is disabled; wait for it to finish, then rerun this installer. No service was killed or source changed.' >&2
      return 1
      ;;
  esac
}
