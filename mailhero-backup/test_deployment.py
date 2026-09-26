from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


class DeploymentTests(unittest.TestCase):
    def test_shell_scripts_parse_and_help_is_read_only(self):
        subprocess.run(["bash", "-n", str(ROOT / "install.sh"), str(ROOT / "run.sh"), str(ROOT / "upgrade-guard.sh")], check=True)
        result = subprocess.run(["bash", str(ROOT / "install.sh"), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("timer is installed disabled", result.stdout)

    def test_short_or_branch_pin_is_rejected_before_any_host_action(self):
        for value in ("main", "abc123", "HEAD"):
            result = subprocess.run(["bash", str(ROOT / "install.sh"), "--commit", value,
                                     "--recipient", "AB" * 20], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("full immutable", result.stderr)

    def test_enable_requires_explicit_recovery_acknowledgement(self):
        result = subprocess.run(["bash", str(ROOT / "install.sh"), "--commit", "a" * 40,
                                 "--recipient", "AB" * 20, "--enable"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--restore-verified", result.stderr)

    def test_service_restricts_write_access_and_uses_pinned_runtime(self):
        unit = (ROOT / "mailhero-backup.service").read_text()
        self.assertIn("User=xiziyi", unit)
        self.assertIn("UMask=0077", unit)
        self.assertIn("NoNewPrivileges=yes", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("ProtectHome=read-only", unit)
        self.assertEqual([line for line in unit.splitlines() if line.startswith("ReadWritePaths=")],
                         ["ReadWritePaths=/home/xiziyi/mail-hero-backup"])
        runtime = (ROOT / "run.sh").read_text()
        self.assertIn('[[ "$actual" == "$expected" ]]', runtime)
        self.assertIn("diff --quiet HEAD -- deploy/backup/mailhero_backup.py", runtime)
        self.assertIn("/usr/bin/python3 -I", runtime)
        self.assertNotIn("docker", unit.lower())
        timer = (ROOT / "mailhero-backup.timer").read_text()
        self.assertIn("OnCalendar=*-*-* 04:17:00 UTC", timer)

    def test_upgrade_guard_precedes_every_checkout_mutation(self):
        installer = (ROOT / "install.sh").read_text()
        guard = installer.index("\nprepare_mailhero_backup_upgrade\n")
        self.assertLess(guard, installer.index("git clone --no-checkout"))
        self.assertLess(guard, installer.index('fetch --depth=1 origin "$commit"'))
        self.assertLess(guard, installer.index('checkout --detach "$commit"'))

    def test_upgrade_guard_stops_scheduling_but_never_kills_a_backup(self):
        # Execute the actual sourced guard with a fake systemctl. The following
        # mutation sentinel must be unreachable for running/unknown service states.
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            systemctl = directory / "systemctl"
            systemctl.write_text("""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ['GUARD_LOG'], 'a') as target:
    target.write(json.dumps(args) + '\\n')
if args == ['show', '--property=LoadState', '--value', 'mailhero-backup.timer']:
    print(os.environ['TIMER_LOAD'])
elif args == ['show', '--property=ActiveState', '--value', 'mailhero-backup.service']:
    if os.environ['SERVICE_STATE'] == 'query-failed':
        sys.exit(1)
    print(os.environ['SERVICE_STATE'])
elif args == ['disable', '--now', 'mailhero-backup.timer']:
    sys.exit(int(os.environ.get('DISABLE_FAIL', '0')))
else:
    sys.exit(99)
""")
            systemctl.chmod(0o755)
            for state in ("inactive", "failed", "active", "activating", "deactivating", "reloading", "query-failed", "unknown"):
                with self.subTest(state=state):
                    log = directory / "calls.jsonl"
                    log.write_text("")
                    env = {**os.environ, "PATH": str(directory) + os.pathsep + os.environ["PATH"],
                           "GUARD_LOG": str(log), "TIMER_LOAD": "loaded", "SERVICE_STATE": state}
                    result = subprocess.run(["bash", "-c", 'set -euo pipefail; source "$1"; prepare_mailhero_backup_upgrade; echo source-mutation',
                                             "guard-test", str(ROOT / "upgrade-guard.sh")], env=env, capture_output=True, text=True)
                    calls = [json.loads(line) for line in log.read_text().splitlines()]
                    self.assertEqual(calls[1], ["disable", "--now", "mailhero-backup.timer"])
                    self.assertEqual(calls[2], ["show", "--property=ActiveState", "--value", "mailhero-backup.service"])
                    self.assertFalse(any("stop" in call or "kill" in call for call in calls))
                    if state in ("inactive", "failed"):
                        self.assertEqual(result.returncode, 0)
                        self.assertIn("source-mutation", result.stdout)
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertNotIn("source-mutation", result.stdout)

            for timer, disable_fail, expected_success in (("not-found", "0", True), ("loaded", "1", False), ("unknown", "0", False)):
                with self.subTest(timer=timer, disable_fail=disable_fail):
                    env = {**os.environ, "PATH": str(directory) + os.pathsep + os.environ["PATH"],
                           "GUARD_LOG": str(directory / "calls.jsonl"), "TIMER_LOAD": timer,
                           "SERVICE_STATE": "inactive", "DISABLE_FAIL": disable_fail}
                    result = subprocess.run(["bash", "-c", 'set -euo pipefail; source "$1"; prepare_mailhero_backup_upgrade; echo source-mutation',
                                             "guard-test", str(ROOT / "upgrade-guard.sh")], env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode == 0, expected_success)
                    self.assertEqual("source-mutation" in result.stdout, expected_success)


if __name__ == "__main__":
    unittest.main()
