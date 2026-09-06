# syntax=docker/dockerfile:1
ARG NEWSLETTER_IMAGE
FROM ${NEWSLETTER_IMAGE} AS newsletter-client

FROM python:3.12.14-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS download
ARG TARGETARCH
# The upstream release metadata publishes this SHA-256 for v0.2.49 amd64.
# https://github.com/aptible/supercronic/releases/tag/v0.2.49
RUN test "$TARGETARCH" = amd64
RUN python - <<'PY'
import hashlib
import pathlib
import urllib.request

url = "https://github.com/aptible/supercronic/releases/download/v0.2.49/supercronic-linux-amd64"
expected = "a53ae236602c7338aba3fbaff40bda6300eae3b9fedb8261eb06cfe3724430c1"
with urllib.request.urlopen(url, timeout=60) as response:
    binary = response.read()
if hashlib.sha256(binary).hexdigest() != expected:
    raise SystemExit("Supercronic release checksum mismatch")
target = pathlib.Path("/supercronic")
target.write_bytes(binary)
target.chmod(0o755)
PY

FROM python:3.12.14-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=UTC
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 trigger
WORKDIR /app
COPY --from=download /supercronic /usr/local/bin/supercronic
# Keep exactly one implementation of orchestration. No SDK, provider packages,
# application data, credentials, fonts, or service configuration enter this image.
COPY --from=newsletter-client /opt/newsletter/.venv/lib/python3.12/site-packages/newsletter/trigger.py /app/trigger.py
COPY crontab /app/crontab
USER 10001:10001
RUN supercronic -test /app/crontab && python /app/trigger.py --help
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s CMD python -c "from pathlib import Path; assert Path('/proc/1/comm').read_text().strip() == 'supercronic'"
# Validate all send capabilities without a network request before starting cron.
# Supercronic is PID 1 and serializes repeated invocations of each scheduled job.
ENTRYPOINT ["/bin/sh", "-ec", "python /app/trigger.py --check-config --send --timeout 7200; exec supercronic -split-logs /app/crontab"]
