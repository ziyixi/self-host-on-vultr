#!/bin/sh
# Host dependencies: POSIX shell, Docker Engine, Docker Compose, Linux stat.
# This helper never installs software, copies credentials, or starts the service.
set -eu

fail() { printf '%s\n' "$*" >&2; exit 1; }
package_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
auth_dir=/home/xiziyi/.local/share/newsletter/codex-auth
data_dir=$package_root/data/newsletter
operation=${1:-check}
case "$operation" in
    check|doctor|login) ;;
    *) fail "Usage: sh newsletter/bootstrap.sh [check|doctor|login]" ;;
esac
[ "$#" -le 1 ] || fail "This helper accepts only one operation; secrets belong in private env files."
command -v docker >/dev/null 2>&1 || fail "Install Docker Engine and its Compose plugin first."
docker info >/dev/null 2>&1 || fail "Docker is unavailable. Start it and check your Docker group access."
newsletter_image=$(docker compose --project-directory "$package_root" --env-file /dev/null \
    -f "$package_root/docker-compose.yml" config --no-env-resolution --no-path-resolution --images newsletter)
case "$newsletter_image" in
    ghcr.io/ziyixi/newsletter@sha256:*) ;;
    *) fail "Pin x-newsletter-image to the tested GHCR SHA-256 digest before bootstrap." ;;
esac
image_digest=${newsletter_image##*@sha256:}
[ "${#image_digest}" -eq 64 ] || fail "The pinned image digest must contain 64 hexadecimal characters."
case "$image_digest" in *[!0123456789abcdef]*) fail "Invalid image digest." ;; esac
architecture=$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$newsletter_image" 2>/dev/null) \
    || fail "Pinned image is not local. Run: docker compose pull newsletter"
[ "$architecture" = linux/amd64 ] || fail "The newsletter image must be Linux/amd64."
[ -d "$auth_dir" ] && [ ! -L "$auth_dir" ] \
    || fail "Create a real dedicated auth directory (UID/GID 10001, mode 700): $auth_dir; see newsletter/README.md."

if [ "$operation" = login ]; then
    # Fail safely rather than rotating a login cache in use by another process.
    for service in newsletter newsletter-trigger; do
        running=$(docker ps --filter "label=com.docker.compose.service=$service" --format '{{.ID}}')
        [ -z "$running" ] || fail "Stop newsletter-trigger and newsletter before login: docker compose stop newsletter-trigger newsletter"
    done
    docker run --rm --pull never --network none --read-only --cap-drop ALL \
        --security-opt no-new-privileges:true --user 10001:10001 \
        --mount "type=bind,src=$auth_dir,dst=/var/lib/newsletter-auth,readonly" \
        --mount "type=bind,src=$package_root/newsletter/doctor.py,dst=/doctor.py,readonly" \
        --entrypoint python "$newsletter_image" /doctor.py --auth-directory-only
    # No --env-file, host home, service data, Docker socket, or published ports.
    # Explicit file storage creates/refreshes only this dedicated login state.
    exec docker run --rm --pull never --interactive --read-only --cap-drop ALL \
        --security-opt no-new-privileges:true --user 10001:10001 \
        --tmpfs /tmp:rw,nosuid,nodev,size=64m,mode=1777 \
        --mount "type=bind,src=$auth_dir,dst=/var/lib/newsletter-auth" \
        --env CODEX_HOME=/var/lib/newsletter-auth --entrypoint python "$newsletter_image" \
        -I /opt/newsletter/.venv/lib/python3.12/site-packages/newsletter/_codex_runtime.py \
        --config 'cli_auth_credentials_store="file"' --config 'forced_login_method="chatgpt"' \
        login --device-auth
fi

for filename in newsletter.env newsletter-trigger.env; do
    private_env=$package_root/env/$filename
    [ -f "$private_env" ] && [ ! -L "$private_env" ] \
        || fail "Create private env/$filename from its example without overwriting an existing file."
    [ "$(stat -c '%a' "$private_env")" = 600 ] \
        || fail "Set mode 600 on env/$filename. Its contents will not be printed."
done
sh "$package_root/newsletter/check-token-pairs.sh" \
    "$package_root/env/newsletter.env" "$package_root/env/newsletter-trigger.env"
[ -d "$data_dir" ] && [ ! -L "$data_dir" ] \
    || fail "Create a NEW real data/newsletter directory (UID/GID 10001, mode 700); never reuse fixture data."
docker run --rm --pull never --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true --user 10001:10001 \
    --env-file "$package_root/env/newsletter.env" \
    --env NEWSLETTER_DATA_DIR=/var/lib/newsletter --env NEWSLETTER_CODEX_HOME=/var/lib/newsletter-auth \
    --mount "type=bind,src=$auth_dir,dst=/var/lib/newsletter-auth,readonly" \
    --mount "type=bind,src=$data_dir,dst=/var/lib/newsletter,readonly" \
    --mount "type=bind,src=$package_root/newsletter/doctor.py,dst=/doctor.py,readonly" \
    --entrypoint python "$newsletter_image" /doctor.py
docker run --rm --pull never --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true --user 10001:10001 \
    --env-file "$package_root/env/newsletter-trigger.env" \
    --env NEWSLETTER_SERVICE_URL=http://newsletter:8080 --env NEWSLETTER_ALLOW_INTERNAL_HTTP=1 \
    --env NEWSLETTER_TIME_ZONE=America/Los_Angeles --entrypoint newsletter-trigger \
    "$newsletter_image" --check-config --send --timeout 3600
printf '%s\n' 'Offline configuration checks passed. This does not prove login validity or provider availability.' \
    'Next: start newsletter only and wait for its authenticated startup preflight/healthcheck.' \
    'Keep newsletter-trigger stopped until the legacy scheduler is disabled and the live test succeeds.'
