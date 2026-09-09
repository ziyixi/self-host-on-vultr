"""Maintenance lifecycle tests with synthetic Docker and provider calls."""

import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "newsletter_maintenance", ROOT / "newsletter/maintenance.py"
)
maintenance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(maintenance)


class FakeCompose(maintenance.Compose):
    def __init__(self, running=maintenance.SERVICES, busy=None, failure=None):
        self.state = set(running)
        self.calls = []
        self.statuses = iter(busy or [False, False, False])
        self.failure = failure

    def running(self):
        return self.state.copy()

    def command(self, arguments, **kwargs):
        self.calls.append(arguments)
        if self.failure == "health" and arguments[:2] == ["start", "--wait"]:
            raise maintenance.MaintenanceError("Health failed")
        if arguments[0] == "stop":
            self.state.difference_update(arguments[1:])
        if arguments[0] == "start":
            self.state.add(arguments[-1])
        return ""

    def admin(self, arguments, *, running=False):
        self.calls.append(["admin", *arguments])
        if arguments == ["status"]:
            return json.dumps(
                {"busy": next(self.statuses), "counts": {"runs": 0}}
            )
        if self.failure == "admin":
            raise maintenance.MaintenanceError("CLI refused")
        if self.failure == "interrupt":
            raise KeyboardInterrupt
        return '{"run_id":"synthetic-child"}\n'


class MaintenanceLifecycleTests(unittest.TestCase):
    def test_only_newsletter_stops_and_original_services_are_restored(self):
        compose = FakeCompose()
        result = maintenance.maintain(
            compose, ["retry-stories", "--request-key", "fixed"]
        )
        self.assertIn("synthetic-child", result)
        self.assertEqual(compose.state, set(maintenance.SERVICES))
        stop_producers = compose.calls.index(
            ["stop", "newsletter-trigger", "newsletter-config-sync"]
        )
        stop_service = compose.calls.index(["stop", "newsletter"])
        operation = compose.calls.index(
            ["admin", "retry-stories", "--request-key", "fixed"]
        )
        health = compose.calls.index(
            ["start", "--wait", "--wait-timeout", "120", "newsletter"]
        )
        trigger = compose.calls.index(["start", "newsletter-trigger"])
        self.assertLess(stop_producers, stop_service)
        self.assertLess(stop_service, operation)
        self.assertLess(operation, health)
        self.assertLess(health, trigger)
        self.assertEqual(compose.calls.count(["admin", "status"]), 3)

    def test_busy_before_pause_does_not_touch_running_containers(self):
        compose = FakeCompose(busy=[True])
        with self.assertRaisesRegex(maintenance.MaintenanceError, "unfinished"):
            maintenance.maintain(compose, ["retry-stories"])
        self.assertEqual(compose.calls, [["admin", "status"]])
        self.assertEqual(compose.state, set(maintenance.SERVICES))

    def test_new_work_after_pause_refuses_without_stopping_service(self):
        compose = FakeCompose(busy=[False, True])
        with self.assertRaisesRegex(maintenance.MaintenanceError, "unfinished"):
            maintenance.maintain(compose, ["retry-stories"])
        self.assertNotIn(["stop", "newsletter"], compose.calls)
        self.assertNotIn(["admin", "retry-stories"], compose.calls)
        self.assertEqual(compose.state, set(maintenance.SERVICES))

    def test_busy_after_service_stop_refuses_operation_and_restores(self):
        compose = FakeCompose(busy=[False, False, True])
        with self.assertRaisesRegex(maintenance.MaintenanceError, "unfinished"):
            maintenance.maintain(compose, ["retry-stories"])
        self.assertNotIn(["admin", "retry-stories"], compose.calls)
        self.assertEqual(compose.state, set(maintenance.SERVICES))

    def test_previously_stopped_containers_are_not_started(self):
        for original in ([], ["newsletter"], ["newsletter-config-sync"]):
            with self.subTest(original=original):
                compose = FakeCompose(running=original)
                maintenance.maintain(compose, ["retry-stories"])
                self.assertEqual(compose.state, set(original))
                for name in set(maintenance.SERVICES) - set(original):
                    self.assertFalse(
                        any(
                            call[0] == "start" and call[-1] == name
                            for call in compose.calls
                        )
                    )

    def test_cli_failure_and_interrupt_restore_original_state(self):
        for failure, expected in (
            ("admin", maintenance.MaintenanceError),
            ("interrupt", KeyboardInterrupt),
        ):
            with self.subTest(failure=failure):
                compose = FakeCompose(failure=failure)
                with self.assertRaises(expected):
                    maintenance.maintain(compose, ["send-verification"])
                self.assertEqual(compose.state, set(maintenance.SERVICES))

    def test_failed_service_health_keeps_trigger_off_and_restores_sync(self):
        compose = FakeCompose(failure="health")
        with self.assertRaisesRegex(
            maintenance.MaintenanceError, "trigger stopped"
        ):
            maintenance.maintain(compose, ["retry-stories"])
        self.assertNotIn("newsletter-trigger", compose.state)
        self.assertIn("newsletter-config-sync", compose.state)
        self.assertNotIn(["start", "newsletter-trigger"], compose.calls)

    def test_orphan_trigger_is_rejected_without_mutation(self):
        compose = FakeCompose(running=["newsletter-trigger"])
        with self.assertRaisesRegex(maintenance.MaintenanceError, "orphaned"):
            maintenance.maintain(compose, ["retry-stories"])
        self.assertEqual(compose.calls, [])


class MaintenanceBoundaryTests(unittest.TestCase):
    def test_running_snapshot_accepts_compose_array_and_json_lines(self):
        rows = [
            {"Service": "newsletter", "State": "running"},
            {"Service": "newsletter-trigger", "State": "exited"},
        ]
        for output in (json.dumps(rows), "\n".join(map(json.dumps, rows))):
            with self.subTest(output=output):
                compose = maintenance.Compose(ROOT)
                with mock.patch.object(compose, "command", return_value=output):
                    self.assertEqual(compose.running(), {"newsletter"})

    def test_paused_restarting_or_scaled_containers_are_not_silently_resumed(
        self,
    ):
        rows = [
            [{"Service": "newsletter", "State": "paused"}],
            [{"Service": "newsletter", "State": "restarting"}],
            [{"Service": "todofy", "State": "running"}],
            [{"Service": "newsletter", "State": "running"}] * 2,
        ]
        for row in rows:
            with self.subTest(row=row):
                compose = maintenance.Compose(ROOT)
                with mock.patch.object(
                    compose, "command", return_value=json.dumps(row)
                ):
                    with self.assertRaisesRegex(
                        maintenance.MaintenanceError, "ambiguous"
                    ):
                        compose.running()

    def test_verification_arguments_preserve_exact_approval_and_request_keys(
        self,
    ):
        arguments = [
            "send-verification",
            "--edition-id",
            "edition",
            "--request-key",
            "stable-key",
            "--expected-render-hash",
            "a" * 64,
            "--after-verification",
            "accepted-predecessor",
        ]
        with mock.patch.object(
            maintenance.sys, "argv", ["maintenance.py", *arguments]
        ):
            self.assertEqual(maintenance.parse_arguments(), arguments)

    def test_status_requires_real_boolean_and_nonnegative_integer_counts(self):
        invalid = [
            "not json",
            "[]",
            "{}",
            '{"busy":0,"counts":{}}',
            '{"busy":false,"counts":{"runs":true}}',
            '{"busy":false,"counts":{"runs":-1}}',
        ]
        compose = maintenance.Compose(ROOT)
        for value in invalid:
            with (
                self.subTest(value=value),
                mock.patch.object(compose, "admin", return_value=value),
            ):
                with self.assertRaisesRegex(
                    maintenance.MaintenanceError, "valid maintenance status"
                ):
                    compose.require_idle(running=False)

    def test_status_errors_do_not_become_idle(self):
        compose = maintenance.Compose(ROOT)
        with mock.patch.object(
            compose, "admin", side_effect=maintenance.MaintenanceError("No DB")
        ):
            with self.assertRaisesRegex(maintenance.MaintenanceError, "No DB"):
                compose.require_idle(running=False)

    def test_compose_errors_do_not_echo_private_env_diagnostics(self):
        secret = "PRIVATE_COMPOSE_SENTINEL"
        failure = subprocess.CalledProcessError(1, ["docker"], secret, secret)
        with mock.patch.object(subprocess, "run", side_effect=failure):
            with self.assertRaises(maintenance.MaintenanceError) as caught:
                maintenance.Compose(ROOT).command(["ps"])
        self.assertNotIn(secret, str(caught.exception))

    def test_mutation_uses_one_off_cli_and_removes_only_its_container(self):
        compose = maintenance.Compose(ROOT)
        unique = "newsletter-maintenance-synthetic"
        with mock.patch.object(
            maintenance.uuid, "uuid4", return_value=mock.Mock(hex="synthetic")
        ):
            with mock.patch.object(
                compose, "command", side_effect=["{}", unique + "\n", ""]
            ) as command:
                compose.admin(["retry-stories", "--request-key", "fixed"])
        arguments = command.call_args_list[0].args[0]
        self.assertEqual(
            arguments[:6], ["run", "--rm", "--no-deps", "-T", "--pull", "never"]
        )
        self.assertEqual(
            arguments[6:12],
            [
                "--name",
                unique,
                "--entrypoint",
                "newsletter",
                "newsletter",
                "admin",
            ],
        )
        self.assertEqual(
            command.call_args_list[-1],
            mock.call(["rm", "--force", unique], docker=True),
        )

    def test_interrupted_one_off_container_is_removed_before_return(self):
        compose = maintenance.Compose(ROOT)
        unique = "newsletter-maintenance-synthetic"
        with mock.patch.object(
            maintenance.uuid, "uuid4", return_value=mock.Mock(hex="synthetic")
        ):
            with mock.patch.object(
                compose,
                "command",
                side_effect=[KeyboardInterrupt, unique + "\n", ""],
            ) as command:
                with self.assertRaises(KeyboardInterrupt):
                    compose.admin(["send-verification"])
        self.assertEqual(
            command.call_args_list[-1],
            mock.call(["rm", "--force", unique], docker=True),
        )

    def test_running_status_uses_exec_without_creating_a_writer(self):
        compose = maintenance.Compose(ROOT)
        with mock.patch.object(
            compose, "command", return_value="{}"
        ) as command:
            compose.admin(["status"], running=True)
        command.assert_called_once_with(
            ["exec", "-T", "newsletter", "newsletter", "admin", "status"]
        )

    def test_second_wrapper_cannot_take_lock_and_lock_can_be_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "newsletter").mkdir()
            with maintenance.maintenance_lock(root):
                with self.assertRaisesRegex(
                    maintenance.MaintenanceError, "Another"
                ):
                    with maintenance.maintenance_lock(root):
                        self.fail("Concurrent wrapper obtained the lock")
            with maintenance.maintenance_lock(root):
                self.assertEqual(
                    (root / "newsletter/.maintenance.lock").stat().st_mode
                    & 0o777,
                    0o600,
                )


if __name__ == "__main__":
    unittest.main()
