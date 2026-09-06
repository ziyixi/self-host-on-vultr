"""Run inside the pinned image with read-only mounts and --network none.

No Codex process, login refresh, database opening, or provider request occurs.
Do not use the application's full startup preflight here: it intentionally writes.
"""

import argparse
import os
from pathlib import Path
import stat
import sys


class DoctorError(ValueError):
    """Operator-facing text constructed here, never an underlying error value."""


def require_private(path: Path, directory: bool) -> None:
    try:
        info = path.lstat()
    except (FileNotFoundError, PermissionError):
        raise DoctorError(f"Missing or inaccessible {path}; see newsletter/README.md.") from None
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    expected_mode = 0o700 if directory else 0o600
    if not expected_type(info.st_mode):
        raise DoctorError(f"{path} must be a real {'directory' if directory else 'file'}, not a symlink.")
    if info.st_uid != 10001 or info.st_gid != 10001 or stat.S_IMODE(info.st_mode) != expected_mode:
        raise DoctorError(f"{path} needs UID/GID 10001 and mode {expected_mode:o}; fix only this dedicated path.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auth-directory-only", action="store_true")
    args = parser.parse_args()
    try:
        from codex_cli_bin import bundled_codex_path
        from newsletter.codex_runtime import check_codex_home, load_sdk

        auth = Path("/var/lib/newsletter-auth")
        require_private(auth, directory=True)
        check_codex_home(auth, Path("/var/lib/newsletter"))
        load_sdk()
        if not os.access(bundled_codex_path(), os.X_OK):
            raise DoctorError("The pinned image is missing an executable bundled Codex runtime.")
        if not args.auth_directory_only:
            from newsletter.settings import Settings

            require_private(auth / "auth.json", directory=False)
            require_private(Path("/var/lib/newsletter"), directory=True)
            try:
                settings = Settings.from_env()
            except (ValueError, TypeError, OverflowError):
                # float/int conversion exceptions include the original value,
                # which might be a credential accidentally pasted into a field.
                raise DoctorError(
                    "Invalid numeric configuration; check NEWSLETTER_JOB_TIMEOUT_SECONDS, "
                    "NEWSLETTER_COLLECTION_TIMEOUT_SECONDS and NEWSLETTER_TODOFY_TOP."
                ) from None
            try:
                settings.validate()
            except (ValueError, KeyError):
                raise DoctorError(
                    "Invalid service settings; compare private env field names with "
                    "env/newsletter.env.example, including three distinct tokens and provider configuration."
                ) from None
            if (settings.mode, settings.editor_backend, settings.notion_backend,
                    settings.todofy_backend, settings.mail_backend) != (
                    "live", "codex", "notion", "todofy", "resend"):
                raise DoctorError("Production requires live Codex, Notion, Todofy and Resend configuration.")
    except Exception as error:
        # Only our explicitly constructed diagnostic text is safe to show.
        # Arbitrary ValueErrors and library errors can contain raw env values.
        message = str(error) if isinstance(error, DoctorError) else type(error).__name__
        print(f"Newsletter bootstrap check failed: {message}", file=sys.stderr)
        print("Fix the dedicated path/configuration; for missing/expired auth use bootstrap.sh login while stopped.", file=sys.stderr)
        return 1
    print("Newsletter filesystem/configuration prerequisites passed; no credentials or providers were contacted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
