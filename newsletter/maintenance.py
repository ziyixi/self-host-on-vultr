"""Run explicit Newsletter maintenance while its normal writers are stopped.

Only the three Newsletter containers are touched. The operator CLI retains its
own service.lock check and idempotency rules; this wrapper never opens SQLite.
"""

import argparse
import contextlib
import fcntl
import json
import os
import pathlib
import signal
import stat
import subprocess
import sys
import uuid
from collections.abc import Iterator

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVICES = ("newsletter", "newsletter-trigger", "newsletter-config-sync")


class MaintenanceError(RuntimeError):
    """A safe diagnostic that never includes Compose output or credentials."""


@contextlib.contextmanager
def maintenance_lock(root: pathlib.Path) -> Iterator[None]:
    """Serialize host wrappers without unlinking the lock inode on release."""
    path = root / "newsletter/.maintenance.lock"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise MaintenanceError(
                "Maintenance lock must be a regular mode-600 file."
            )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise MaintenanceError(
                "Another Newsletter maintenance wrapper is active."
            ) from None
        yield
    finally:
        os.close(descriptor)


class Compose:
    """Run project-scoped Compose commands with bounded, private diagnostics."""

    def __init__(self, root: pathlib.Path) -> None:
        self.prefix = [
            "docker",
            "compose",
            "--project-directory",
            str(root),
            "-f",
            str(root / "docker-compose.yml"),
        ]

    def command(self, arguments: list[str], *, docker: bool = False) -> str:
        """Return stdout without exposing untrusted failure output."""
        prefix = ["docker"] if docker else self.prefix
        try:
            result = subprocess.run(
                prefix + arguments,
                check=True,
                capture_output=True,
                text=True,
                timeout=180,
            )
        except (OSError, subprocess.SubprocessError):
            raise MaintenanceError(
                "Docker operation failed; inspect Newsletter container "
                "state locally."
            ) from None
        return result.stdout

    def running(self) -> set[str]:
        """Snapshot running Newsletter containers; reject transient states."""
        raw = self.command(["ps", "--all", "--format", "json", *SERVICES])
        try:
            if raw.lstrip().startswith("["):
                rows = json.loads(raw)
            else:
                rows = [json.loads(line) for line in raw.splitlines() if line]
            running = set()
            seen = set()
            for row in rows:
                service, state = row["Service"], row["State"]
                if service not in SERVICES or service in seen:
                    raise ValueError
                seen.add(service)
                if state == "running":
                    running.add(service)
                elif state not in {"exited", "created"}:
                    raise ValueError
            return running
        except (ValueError, KeyError, TypeError):
            raise MaintenanceError(
                "Newsletter container state is ambiguous or changing; "
                "retry when stable."
            ) from None

    def admin(self, arguments: list[str], *, running: bool = False) -> str:
        """Run the image CLI and remove interrupted one-off containers."""
        if running:
            return self.command(
                [
                    "exec",
                    "-T",
                    "newsletter",
                    "newsletter",
                    "admin",
                    *arguments,
                ]
            )
        name = "newsletter-maintenance-" + uuid.uuid4().hex
        try:
            return self.command(
                [
                    "run",
                    "--rm",
                    "--no-deps",
                    "-T",
                    "--pull",
                    "never",
                    "--name",
                    name,
                    "--entrypoint",
                    "newsletter",
                    "newsletter",
                    "admin",
                    *arguments,
                ]
            )
        finally:
            names = self.command(
                [
                    "ps",
                    "--all",
                    "--filter",
                    "name=" + name,
                    "--format",
                    "{{.Names}}",
                ],
                docker=True,
            ).splitlines()
            if name in names:
                self.command(["rm", "--force", name], docker=True)

    def require_idle(self, *, running: bool) -> dict[str, object]:
        """Refuse missing state, malformed diagnostics, and unfinished work."""
        try:
            status = json.loads(self.admin(["status"], running=running))
            if (
                not isinstance(status, dict)
                or type(status.get("busy")) is not bool
            ):
                raise ValueError
            counts = status.get("counts")
            if not isinstance(counts, dict) or any(
                not isinstance(key, str) or type(value) is not int or value < 0
                for key, value in counts.items()
            ):
                raise ValueError
        except (ValueError, TypeError):
            raise MaintenanceError(
                "Newsletter did not return a valid maintenance status."
            ) from None
        if status["busy"]:
            raise MaintenanceError(
                "Newsletter has unfinished work or an external submission; "
                "maintenance refused."
            )
        return status

    def restore(self, original: set[str]) -> None:
        """Restore original containers, keeping trigger off if health fails."""
        failed = []
        service_ready = "newsletter" not in original
        if "newsletter" in original:
            try:
                self.command(
                    ["start", "--wait", "--wait-timeout", "120", "newsletter"]
                )
                service_ready = True
            except MaintenanceError:
                failed.append("newsletter")
        if "newsletter-config-sync" in original:
            try:
                self.command(["start", "newsletter-config-sync"])
            except MaintenanceError:
                failed.append("newsletter-config-sync")
        if "newsletter-trigger" in original and service_ready:
            try:
                self.command(["start", "newsletter-trigger"])
            except MaintenanceError:
                failed.append("newsletter-trigger")
        if failed:
            raise MaintenanceError(
                "Could not restore "
                + ", ".join(failed)
                + "; keep the trigger stopped until Newsletter is healthy. "
                "Inspect docker compose ps newsletter newsletter-trigger "
                "newsletter-config-sync, then start only originally "
                "running containers."
            )


def maintain(compose: Compose, arguments: list[str]) -> str:
    """Quiesce producers, prove idle, stop the service, and invoke its CLI."""
    original = compose.running()
    if "newsletter-trigger" in original and "newsletter" not in original:
        raise MaintenanceError(
            "Stop the orphaned Newsletter trigger before maintenance."
        )
    # Do not interrupt an already active cron client or provider submission.
    compose.require_idle(running="newsletter" in original)
    paused = [name for name in SERVICES[1:] if name in original]
    try:
        if paused:
            compose.command(["stop", *paused])
        compose.require_idle(running="newsletter" in original)
        if "newsletter" in original:
            compose.command(["stop", "newsletter"])
        if compose.running():
            raise MaintenanceError(
                "Newsletter containers did not stop; maintenance refused."
            )
        # Recheck under stopped-container conditions. The mutating CLI then
        # takes service.lock, checks state itself, and never runs recovery.
        compose.require_idle(running=False)
        return compose.admin(arguments)
    finally:
        compose.restore(original)


def parse_arguments() -> list[str]:
    """Accept only the two explicit maintenance capabilities and their IDs."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    retry = commands.add_parser("retry-stories")
    retry.add_argument("--parent-run-id", required=True)
    retry.add_argument("--request-key", required=True)
    retry.add_argument("--issue-date", required=True)
    verification = commands.add_parser("send-verification")
    verification.add_argument("--edition-id", required=True)
    verification.add_argument("--request-key", required=True)
    verification.add_argument("--expected-render-hash", required=True)
    verification.add_argument("--after-verification")
    args = vars(parser.parse_args())
    operation = args.pop("operation")
    result = [operation]
    for name, value in args.items():
        if value is not None:
            result.extend(["--" + name.replace("_", "-"), value])
    return result


def interrupted(signum: int, frame: object) -> None:
    """Turn termination into an exception so state restoration can run."""
    del signum, frame
    raise KeyboardInterrupt


def main() -> int:
    """Run maintenance without exposing private Docker diagnostics."""
    arguments = parse_arguments()
    signal.signal(signal.SIGTERM, interrupted)
    try:
        with maintenance_lock(ROOT):
            output = maintain(Compose(ROOT), arguments)
    except KeyboardInterrupt:
        print(
            "Newsletter maintenance interrupted; inspect container state.",
            file=sys.stderr,
        )
        return 130
    except (MaintenanceError, OSError) as error:
        message = (
            str(error)
            if isinstance(error, MaintenanceError)
            else "Host lock is unavailable."
        )
        print(message, file=sys.stderr)
        return 1
    print(output, end="" if output.endswith("\n") else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
