#!/bin/sh
# Compare raw env-file values as data, without sourcing, expanding, or logging them.
set -eu
[ "$#" -eq 2 ] || { printf '%s\n' 'Usage: check-token-pairs.sh SERVICE_ENV TRIGGER_ENV' >&2; exit 1; }
awk '
    FILENAME == ARGV[2] && $0 !~ /^[[:space:]]*(#|$)/ &&
        $0 !~ /^NEWSLETTER_(EDITOR|SEND)_TOKEN=/ {
        unexpected_trigger_line = 1
        next
    }
    /^NEWSLETTER_(EDITOR|SEND)_TOKEN=/ {
        separator = index($0, "=")
        key = substr($0, 1, separator - 1)
        value = substr($0, separator + 1)
        if (FILENAME == ARGV[1]) {
            service[key] = value
            service_count[key]++
        } else {
            trigger[key] = value
            trigger_count[key]++
        }
    }
    END {
        if (unexpected_trigger_line) {
            print "Trigger env may contain only editor/send token assignments, comments and blank lines." > "/dev/stderr"
            failed = 1
        }
        keys[1] = "NEWSLETTER_EDITOR_TOKEN"
        keys[2] = "NEWSLETTER_SEND_TOKEN"
        for (i = 1; i <= 2; i++) {
            key = keys[i]
            if (service_count[key] != 1 || trigger_count[key] != 1 ||
                service[key] == "" || service[key] != trigger[key]) {
                print key " must appear once and match between the two private env files." > "/dev/stderr"
                failed = 1
            }
        }
        exit failed
    }
' "$1" "$2"
