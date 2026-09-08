"""Offline deployment-contract checks; never read private env or contact a daemon."""

import json
import importlib.util
import contextlib
from datetime import date, datetime, timedelta, timezone
import io
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def load_compose_config():
    """Fail before parsing on versions that can resolve supposedly excluded env files."""
    try:
        version = subprocess.run(["docker", "compose", "version", "--short"],
                                 text=True, capture_output=True, check=False)
    except OSError:
        raise RuntimeError("Install Docker Compose 5.1.0 or newer; CI pins the production version 5.1.0.") from None
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?", version.stdout.strip())
    if version.returncode or not match or tuple(map(int, match.group(1, 2, 3))) < (5, 1, 0):
        # Do not echo arbitrary command output or try resolving any env file.
        raise RuntimeError(
            "Docker Compose 5.1.0 or newer is required for secret-free config checks. "
            "Compose 2.38.2 can resolve env files despite --no-env-resolution; install the pinned CI version."
        )
    result = subprocess.run(
        ["docker", "compose", "--env-file", "/dev/null", "config",
         "--no-env-resolution", "--no-path-resolution", "--format", "json"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    if result.returncode:
        detail = "Check the versioned Compose syntax and supported options."
        if "env file" in result.stderr and "not found" in result.stderr:
            detail = "Env exclusion was not honored; check the Compose version, not private env files."
        elif "unknown flag" in result.stderr:
            detail = "The Compose binary does not support required config options."
        # stderr can contain interpolated input, so classify it but never print it.
        raise RuntimeError(f"Compose config failed with exit code {result.returncode}. {detail}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError("Compose did not produce valid JSON; configuration output was suppressed.") from None


class DeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_compose_config()
        cls.services = cls.config["services"]

    def test_immutable_service_and_client_are_identical(self):
        image = self.services["newsletter"]["image"]
        self.assertRegex(image, r"^ghcr\.io/ziyixi/newsletter@sha256:[0-9a-f]{64}$")
        self.assertEqual(image, self.services["newsletter-trigger"]["build"]["args"]["NEWSLETTER_IMAGE"])
        self.assertEqual(image, self.services["newsletter-config-sync"]["image"])

    def test_network_has_egress_but_no_exposure(self):
        self.assertFalse(self.config["networks"]["newsletter"].get("internal", False))
        for name in ("newsletter", "newsletter-trigger", "newsletter-config-sync"):
            with self.subTest(name=name):
                service = self.services[name]
                self.assertFalse(service.get("ports"))
                self.assertNotEqual(service.get("network_mode"), "host")
                self.assertEqual(set(service["networks"]), {"newsletter"})

    def test_container_security_and_resource_limits(self):
        self.assertTrue(self.services["newsletter"]["init"])
        for name in ("newsletter", "newsletter-trigger", "newsletter-config-sync"):
            with self.subTest(name=name):
                service = self.services[name]
                self.assertEqual(service["platform"], "linux/amd64")
                self.assertEqual(service["user"], "10001:10001")
                self.assertTrue(service["read_only"])
                self.assertIn("ALL", service["cap_drop"])
                self.assertIn("no-new-privileges:true", service["security_opt"])
                self.assertGreater(int(service["mem_limit"]), 0)
                self.assertGreater(service["pids_limit"], 0)
                self.assertGreater(float(service["cpus"]), 0)
                self.assertFalse(service.get("privileged", False))

    def test_auth_outside_backup_roots_and_no_auto_created_mounts(self):
        mounts = self.services["newsletter"]["volumes"]
        by_target = {mount["target"]: mount for mount in mounts}
        auth = by_target["/var/lib/newsletter-auth"]
        self.assertEqual(auth["source"], "/home/xiziyi/.local/share/newsletter/codex-auth")
        self.assertEqual(by_target["/var/lib/newsletter"]["source"], "./data/newsletter")
        for mount in mounts:
            self.assertFalse(mount["bind"]["create_host_path"])
            self.assertNotIn("docker.sock", mount["source"])
        self.assertFalse(self.services["newsletter-trigger"].get("volumes"))

    def test_content_configuration_writer_is_isolated_from_reader_and_provider_state(self):
        service = self.services["newsletter"]
        sync = self.services["newsletter-config-sync"]
        target = "/var/lib/newsletter-config"
        self.assertEqual(service["environment"]["NEWSLETTER_CONTENT_CONFIG_DIR"], target)
        self.assertEqual(sync["environment"], {
            "NEWSLETTER_CONTENT_CONFIG_DIR": target,
            "NEWSLETTER_CONFIG_REPOSITORY": "ziyixi/newsletter",
            "NEWSLETTER_CONFIG_POLL_SECONDS": "900",
        })
        reader_mount = next(mount for mount in service["volumes"] if mount["target"] == target)
        self.assertEqual(reader_mount["source"], "./config/newsletter")
        self.assertTrue(reader_mount["read_only"])
        self.assertEqual(len(sync["volumes"]), 1)
        writer_mount = sync["volumes"][0]
        self.assertEqual(writer_mount["source"], reader_mount["source"])
        self.assertEqual(writer_mount["target"], target)
        self.assertFalse(writer_mount.get("read_only", False))
        self.assertFalse(writer_mount["bind"]["create_host_path"])
        self.assertEqual(sync["entrypoint"], ["python", "-m", "newsletter.config_sync"])
        self.assertEqual(sync["command"], ["run"])
        self.assertFalse(sync.get("depends_on"))
        self.assertNotIn("newsletter-config-sync", service.get("depends_on", {}))
        self.assertEqual(sync["healthcheck"]["test"],
                         ["CMD", "python", "-m", "newsletter.config_sync", "health"])
        self.assertFalse(self.services["newsletter-trigger"].get("volumes"))
        backup = self.services["backup"]["volumes"]
        config_backup = next(mount for mount in backup if mount["target"] == "/backup/newsletter-config")
        self.assertTrue(config_backup["read_only"])
        self.assertEqual(config_backup["source"], "./config/newsletter")

    def test_public_config_sync_receives_no_credentials_or_env_file(self):
        sync = self.services["newsletter-config-sync"]
        self.assertFalse(sync.get("env_file"))
        self.assertNotIn("NEWSLETTER_CONFIG_GITHUB_TOKEN", self.services["newsletter"]["environment"])
        self.assertNotIn("NEWSLETTER_CONFIG_GITHUB_TOKEN", self.services["newsletter-trigger"]["environment"])

    def test_config_operator_docs_use_explicit_seed_and_persistent_local_pin(self):
        docs = (ROOT / "newsletter/README.md").read_text()
        for operation in ("seed", "once", "status", "pin", "unpin"):
            self.assertIn("newsletter-config-sync " + operation, docs)
        self.assertIn("last validated", docs)
        self.assertIn("published", docs)
        self.assertIn("do not run `update.sh`", docs)
        self.assertIn("no SQLite", docs)
        self.assertIn("config/newsletter", docs)

    def test_env_files_are_raw_and_separate(self):
        for name in ("newsletter", "newsletter-trigger"):
            self.assertEqual(self.services[name]["env_file"],
                             [{"path": f"./env/{name}.env", "format": "raw"}])
        example = (ROOT / "env/newsletter-trigger.env.example").read_text()
        keys = {line.split("=", 1)[0] for line in example.splitlines()
                if line and not line.startswith("#")}
        self.assertEqual(keys, {"NEWSLETTER_EDITOR_TOKEN", "NEWSLETTER_SEND_TOKEN"})

    def test_notion_dual_database_example_keeps_secrets_blank_and_privacy_opt_in(self):
        example = (ROOT / "env/newsletter.env.example").read_text()
        values = dict(line.split("=", 1) for line in example.splitlines()
                      if line and not line.startswith("#"))
        self.assertEqual(values["NEWSLETTER_NOTION"], "notion")
        self.assertEqual(values["NEWSLETTER_WORKFLOW"], "dag")
        self.assertEqual(values["NOTION_TOKEN"], "")
        self.assertEqual(values["NOTION_MATERIALS_DATA_SOURCE_ID"], "")
        self.assertEqual(values["NOTION_EDITIONS_DATA_SOURCE_ID"], "")
        self.assertEqual(values["NEWSLETTER_NOTION_ARCHIVE_PRIVATE"], "false")
        self.assertNotIn("NOTION_DATA_SOURCE_ID", values)
        self.assertIn("# NOTION_DATA_SOURCE_ID=", example)
        for name in ("NEWSLETTER_INGEST_TOKEN", "NEWSLETTER_EDITOR_TOKEN", "NEWSLETTER_SEND_TOKEN",
                     "RESEND_API_KEY", "TODO_API_USER", "TODO_API_PASSWORD"):
            self.assertEqual(values[name], "")
        # Notion authority belongs only to the service, never the trigger.
        trigger = (ROOT / "env/newsletter-trigger.env.example").read_text()
        self.assertNotRegex(trigger, r"(?m)^(?:NOTION_|NEWSLETTER_NOTION)")

    def test_notion_operator_commands_do_not_start_content_or_a_second_scheduler(self):
        documentation = (ROOT / "newsletter/README.md").read_text()
        setup = ("docker compose run --rm --no-deps --entrypoint python newsletter "
                 "-m newsletter.notion_cli setup")
        self.assertIn(setup + "\n", documentation)
        self.assertIn(setup + " --apply\n", documentation)
        self.assertIn("docker compose exec -T newsletter python -m newsletter.notion_cli status",
                      documentation)
        self.assertIn("Neither `update.sh` nor normal service startup implicitly creates",
                      documentation)
        self.assertIn("The existing service need not be stopped", documentation)
        self.assertIn("`status` opens SQLite read-only and makes no provider calls.", documentation)
        self.assertIn("Do not delete SQLite packets, runs, frozen", documentation)
        for relative in ("update.sh", "newsletter/bootstrap.sh", "newsletter-trigger/crontab",
                         "newsletter-trigger/Dockerfile"):
            with self.subTest(path=relative):
                self.assertNotIn("notion_cli", (ROOT / relative).read_text())
        self.assertNotIn("NOTION_TOKEN", self.services["newsletter-trigger"]["environment"])
        self.assertNotIn("cron", str(self.services["newsletter"].get("command", "")).lower())

    def test_cron_schedule_and_strict_internal_origin(self):
        environment = self.services["newsletter-trigger"]["environment"]
        self.assertEqual(environment["TZ"], "America/Los_Angeles")
        self.assertEqual(environment["NEWSLETTER_TIME_ZONE"], "America/Los_Angeles")
        self.assertEqual(environment["NEWSLETTER_SERVICE_URL"], "http://newsletter:8080")
        self.assertEqual(environment["NEWSLETTER_ALLOW_INTERNAL_HTTP"], "1")
        lines = [line for line in (ROOT / "newsletter-trigger/crontab").read_text().splitlines()
                 if line and not line.startswith("#")]
        self.assertEqual(lines, ["0 7 * * * /usr/local/bin/python /app/trigger.py --send --timeout 7200"])

    def test_morning_budget_has_margin_in_summer_winter_and_dst_transition_days(self):
        environment = self.services["newsletter-trigger"]["environment"]
        zone = ZoneInfo(environment["TZ"])
        cron = next(line for line in (ROOT / "newsletter-trigger/crontab").read_text().splitlines()
                    if line and not line.startswith("#"))
        minute, hour, day, month, weekday = cron.split()[:5]
        self.assertEqual((day, month, weekday), ("*", "*", "*"))
        client_seconds = int(re.search(r"--timeout (\d+)", cron).group(1))
        workflow_seconds = int(self.services["newsletter"]["environment"][
            "NEWSLETTER_WORKFLOW_TIMEOUT_SECONDS"])
        # IANA-zone checks for the configured daily wall time, not a model of
        # provider/inbox reliability. The image separately parses real Supercronic.
        cases = (("2026-01-15", 15), ("2026-07-15", 14),
                 ("2026-03-07", 15), ("2026-03-08", 14), ("2026-03-09", 14),
                 ("2026-10-31", 14), ("2026-11-01", 15), ("2026-11-02", 15))
        for value, utc_hour in cases:
            with self.subTest(day=value):
                issue_day = date.fromisoformat(value)
                start = datetime(issue_day.year, issue_day.month, issue_day.day,
                                 int(hour), int(minute), tzinfo=zone)
                start_utc = start.astimezone(timezone.utc)
                self.assertEqual(start_utc.hour, utc_hour)
                self.assertEqual(start_utc.date(), issue_day)
                self.assertEqual(start_utc.astimezone(zone), start)
                # At 07:00, fold=0/1 must denote the same instant: there is no
                # ambiguous repeated-hour occurrence on the autumn transition.
                self.assertEqual(start.replace(fold=1).astimezone(timezone.utc), start_utc)
                content_end = (start_utc + timedelta(seconds=workflow_seconds)).astimezone(zone)
                client_end = (start_utc + timedelta(seconds=client_seconds)).astimezone(zone)
                target = start.replace(hour=9, minute=30)
                self.assertEqual((content_end.hour, content_end.minute), (8, 30))
                self.assertEqual((client_end.hour, client_end.minute), (9, 0))
                self.assertEqual(client_end.date(), issue_day)
                self.assertLess(client_end, target)
                self.assertGreaterEqual((target - client_end).total_seconds(), 1800)

    def test_local_daily_start_tracks_dst_without_a_fixed_utc_offset(self):
        zone = ZoneInfo(self.services["newsletter-trigger"]["environment"]["TZ"])
        for first, second, elapsed_hours in (("2026-03-07", "2026-03-08", 23),
                                              ("2026-10-31", "2026-11-01", 25)):
            with self.subTest(first=first):
                starts = [datetime.fromisoformat(value + "T07:00:00").replace(tzinfo=zone)
                          .astimezone(timezone.utc) for value in (first, second)]
                self.assertEqual((starts[1] - starts[0]).total_seconds(), elapsed_hours * 3600)

    def test_trigger_image_has_named_zone_data_and_matches_compose(self):
        dockerfile = (ROOT / "newsletter-trigger/Dockerfile").read_text()
        timezone_name = self.services["newsletter-trigger"]["environment"]["TZ"]
        self.assertIn("TZ=" + timezone_name, dockerfile)
        self.assertIn("ca-certificates tzdata", dockerfile)
        self.assertIn("test -r /usr/share/zoneinfo/" + timezone_name, dockerfile)
        self.assertIn("supercronic -test /app/crontab", dockerfile)

    def test_workflow_and_trigger_deadlines_stay_aligned(self):
        environment = self.services["newsletter"]["environment"]
        self.assertEqual(environment["NEWSLETTER_WORKFLOW"], "dag")
        self.assertEqual(environment["NEWSLETTER_WORKFLOW_TIMEOUT_SECONDS"], "5400")
        for relative in ("newsletter-trigger/crontab", "newsletter-trigger/Dockerfile",
                         ".github/workflows/newsletter.yml", "newsletter/bootstrap.sh"):
            with self.subTest(path=relative):
                deadlines = re.findall(r"--timeout (\d+)", (ROOT / relative).read_text())
                self.assertEqual(deadlines, ["7200"])
                self.assertGreaterEqual(int(deadlines[0]),
                                        int(environment["NEWSLETTER_WORKFLOW_TIMEOUT_SECONDS"]) + 1800)
        example = (ROOT / "env/newsletter.env.example").read_text()
        self.assertIn("NEWSLETTER_WORKFLOW=dag\n", example)
        self.assertIn("NEWSLETTER_WORKFLOW_TIMEOUT_SECONDS=5400\n", example)

    def test_thin_image_uses_only_one_orchestration_implementation(self):
        dockerfile = (ROOT / "newsletter-trigger/Dockerfile").read_text()
        self.assertIn("site-packages/newsletter/trigger.py /app/trigger.py", dockerfile)
        self.assertNotIn("COPY .", dockerfile)
        self.assertIn("--check-config --send", dockerfile)
        self.assertNotIn("-overlapping", dockerfile)
        self.assertIn("a53ae236602c7338aba3fbaff40bda6300eae3b9fedb8261eb06cfe3724430c1", dockerfile)
        self.assertEqual((ROOT / "newsletter-trigger/.dockerignore").read_text().splitlines(),
                         ["**", "!Dockerfile", "!crontab"])

    def test_login_helper_uses_image_and_never_auto_repairs_state(self):
        script = (ROOT / "newsletter/bootstrap.sh").read_text()
        subprocess.run(["sh", "-n", str(ROOT / "newsletter/bootstrap.sh")], check=True)
        self.assertIn("_codex_runtime.py", script)
        self.assertIn("login --device-auth", script)
        self.assertIn("--network none", script)
        self.assertNotRegex(script, r"(?m)^\s*(sudo |chmod |chown |mkdir |cp |rm |source )")
        self.assertNotIn("docker compose down", script)

    def test_image_copy_modes_do_not_inherit_private_checkout_umask(self):
        dockerfile = (ROOT / "newsletter-trigger/Dockerfile").read_text()
        expected = [
            "COPY --from=download --chmod=0755 /supercronic /usr/local/bin/supercronic",
            "COPY --from=newsletter-client --chmod=0644 /opt/newsletter/.venv/lib/python3.12/site-packages/newsletter/trigger.py /app/trigger.py",
            "COPY --chmod=0644 crontab /app/crontab",
        ]
        copies = [line for line in dockerfile.splitlines() if line.startswith("COPY ")]
        self.assertEqual(copies, expected)
        for instruction in expected:
            self.assertLess(dockerfile.index(instruction), dockerfile.index("USER 10001:10001"))

    def test_private_filenames_ignored_but_examples_visible(self):
        private = ["env/newsletter.env", "env/newsletter-trigger.env", ".env.production",
                   "env/service.env.backup", "auth.json", "nested/credentials.json",
                   "nested/private.key", "data/newsletter/state.sqlite3", "config/newsletter/active.json",
                   "config/newsletter/releases/test/bundle.json"]
        result = subprocess.run(["git", "check-ignore", "--stdin"], cwd=ROOT,
                                input="\n".join(private) + "\n", text=True,
                                capture_output=True, check=True)
        self.assertEqual(set(result.stdout.splitlines()), set(private))
        result = subprocess.run(["git", "check-ignore", "--stdin"], cwd=ROOT,
                                input="env/newsletter.env.example\nenv/newsletter-trigger.env.example\n",
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)

    def test_existing_services_remain_present(self):
        expected = {"unami", "unami-db", "todofy", "todofy-llm", "todofy-todo",
                    "todofy-database", "stirling", "slash", "flowday", "backup"}
        self.assertTrue(expected.issubset(self.services))


class ComposeCompatibilityTests(unittest.TestCase):
    def test_old_compose_fails_before_any_config_or_env_parsing(self):
        result = subprocess.CompletedProcess([], 0, "2.38.2\n", "")
        with patch.object(subprocess, "run", return_value=result) as run:
            with self.assertRaisesRegex(RuntimeError, "5.1.0 or newer"):
                load_compose_config()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ["docker", "compose", "version", "--short"])

    def test_config_failure_gives_safe_actionable_diagnostic(self):
        sentinel = "PRIVATE_SENTINEL_IN_COMPOSE_DIAGNOSTIC"
        results = [subprocess.CompletedProcess([], 0, "5.1.0\n", ""),
                   subprocess.CompletedProcess([], 1, sentinel, "env file " + sentinel + " not found")]
        with patch.object(subprocess, "run", side_effect=results):
            with self.assertRaises(RuntimeError) as caught:
                load_compose_config()
        self.assertIn("Env exclusion was not honored", str(caught.exception))
        self.assertNotIn(sentinel, str(caught.exception))


class DoctorPermissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("newsletter_doctor", ROOT / "newsletter/doctor.py")
        cls.doctor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.doctor)

    def test_accepts_only_dedicated_uid_and_private_modes(self):
        for directory, mode in ((True, stat.S_IFDIR | 0o700), (False, stat.S_IFREG | 0o600)):
            with self.subTest(directory=directory):
                info = SimpleNamespace(st_uid=10001, st_gid=10001, st_mode=mode)
                with patch.object(Path, "lstat", return_value=info):
                    self.doctor.require_private(Path("/private/example"), directory)

    def test_rejects_wrong_owner_mode_and_symlinks_without_repair(self):
        for uid, gid, mode in ((1000, 10001, stat.S_IFREG | 0o600),
                               (10001, 1000, stat.S_IFREG | 0o600),
                               (10001, 10001, stat.S_IFREG | 0o644),
                               (10001, 10001, stat.S_IFLNK | 0o600)):
            with self.subTest(uid=uid, gid=gid, mode=mode):
                info = SimpleNamespace(st_uid=uid, st_gid=gid, st_mode=mode)
                with patch.object(Path, "lstat", return_value=info):
                    with self.assertRaises(ValueError):
                        self.doctor.require_private(Path("/private/example"), False)

    def test_missing_or_inaccessible_state_has_actionable_error(self):
        for error in (FileNotFoundError, PermissionError):
            with patch.object(Path, "lstat", side_effect=error):
                with self.assertRaisesRegex(ValueError, "newsletter/README.md"):
                    self.doctor.require_private(Path("/private/example"), False)

    def test_bad_numeric_env_never_echoes_its_value(self):
        sentinel = "SECRET_SENTINEL_MUST_NOT_APPEAR_IN_DOCTOR_OUTPUT"

        class NumericSettings:
            @classmethod
            def from_env(cls):
                return float(os.environ["NEWSLETTER_JOB_TIMEOUT_SECONDS"])

        modules = {
            "codex_cli_bin": SimpleNamespace(bundled_codex_path=lambda: "/runtime/codex"),
            "newsletter": SimpleNamespace(),
            "newsletter.codex_runtime": SimpleNamespace(
                check_codex_home=lambda *_: None, load_sdk=lambda: None),
            "newsletter.settings": SimpleNamespace(Settings=NumericSettings),
        }
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, modules))
            stack.enter_context(patch.dict(os.environ, {"NEWSLETTER_JOB_TIMEOUT_SECONDS": sentinel}))
            stack.enter_context(patch.object(sys, "argv", ["doctor.py"]))
            stack.enter_context(patch.object(self.doctor, "require_private"))
            stack.enter_context(patch.object(self.doctor.os, "access", return_value=True))
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            result = self.doctor.main()
        self.assertEqual(result, 1)
        self.assertIn("Invalid numeric configuration", stderr.getvalue())
        self.assertNotIn(sentinel, stdout.getvalue() + stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_untrusted_library_value_error_never_echoes_its_value(self):
        sentinel = "SECRET_SENTINEL_IN_LIBRARY_ERROR"

        def fail_sdk():
            raise ValueError(sentinel)

        modules = {
            "codex_cli_bin": SimpleNamespace(bundled_codex_path=lambda: "/runtime/codex"),
            "newsletter": SimpleNamespace(),
            "newsletter.codex_runtime": SimpleNamespace(
                check_codex_home=lambda *_: None, load_sdk=fail_sdk),
        }
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, modules))
            stack.enter_context(patch.object(sys, "argv", ["doctor.py"]))
            stack.enter_context(patch.object(self.doctor, "require_private"))
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            result = self.doctor.main()
        self.assertEqual(result, 1)
        self.assertNotIn(sentinel, stdout.getvalue() + stderr.getvalue())
        self.assertIn("ValueError", stderr.getvalue())


class TokenPairTests(unittest.TestCase):
    def compare(self, service, trigger):
        # These synthetic test values never come from an operator's env files.
        with tempfile.TemporaryDirectory(prefix="newsletter-config-test-") as directory:
            service_path = Path(directory) / "service config"
            trigger_path = Path(directory) / "trigger config"
            service_path.write_text(service)
            trigger_path.write_text(trigger)
            return subprocess.run(
                ["sh", str(ROOT / "newsletter/check-token-pairs.sh"),
                 str(service_path), str(trigger_path)],
                text=True, capture_output=True,
            )

    def test_matching_raw_tokens_do_not_expand_or_leak(self):
        values = ('NEWSLETTER_EDITOR_TOKEN=test-$(printf DO_NOT_EXECUTE)-$HOME-"=literal\\token\n'
                  "NEWSLETTER_SEND_TOKEN=test-distinct-send-token-abcdef\n")
        result = self.compare(values, values)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout + result.stderr, "")

    def test_mismatch_fails_with_key_name_only(self):
        values = "NEWSLETTER_EDITOR_TOKEN=private-test-one\nNEWSLETTER_SEND_TOKEN=private-test-two\n"
        result = self.compare(values, values.replace("private-test-one", "different-test-secret"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("NEWSLETTER_EDITOR_TOKEN", result.stderr)
        for token in ("private-test-one", "private-test-two", "different-test-secret"):
            self.assertNotIn(token, result.stdout + result.stderr)

    def test_missing_empty_or_duplicate_tokens_fail(self):
        values = "NEWSLETTER_EDITOR_TOKEN=test-editor\nNEWSLETTER_SEND_TOKEN=test-send\n"
        for malformed in ("", "NEWSLETTER_EDITOR_TOKEN=\nNEWSLETTER_SEND_TOKEN=test-send\n",
                          values + "NEWSLETTER_EDITOR_TOKEN=test-editor\n"):
            with self.subTest(malformed=malformed):
                result = self.compare(values, malformed)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("test-editor", result.stdout + result.stderr)

    def test_trigger_rejects_other_secrets_without_echoing_key_or_value(self):
        values = "NEWSLETTER_EDITOR_TOKEN=test-editor\nNEWSLETTER_SEND_TOKEN=test-send\n"
        sentinel = "SECRET_PROVIDER_SENTINEL_MUST_NOT_BE_ECHOED"
        for extra in ("RESEND_API_KEY=" + sentinel, sentinel, "UNRECOGNIZED_KEY=" + sentinel):
            with self.subTest(extra_kind=extra.split("=", 1)[0]):
                result = self.compare(values, values + extra + "\n")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Trigger env may contain only", result.stderr)
                for forbidden in (sentinel, "RESEND_API_KEY", "UNRECOGNIZED_KEY"):
                    self.assertNotIn(forbidden, result.stdout + result.stderr)

    def test_trigger_allows_comments_and_blank_lines(self):
        values = "NEWSLETTER_EDITOR_TOKEN=test-editor\nNEWSLETTER_SEND_TOKEN=test-send\n"
        result = self.compare(values, "# Private capabilities\n  # comment\n\n  \n" + values)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout + result.stderr, "")


if __name__ == "__main__":
    unittest.main()
