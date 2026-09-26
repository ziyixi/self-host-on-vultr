from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parent


class DeploymentTests(unittest.TestCase):
    def test_shell_scripts_parse_and_help_is_read_only(self):
        subprocess.run(["bash", "-n", str(ROOT / "install.sh"), str(ROOT / "run.sh")], check=True)
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


if __name__ == "__main__":
    unittest.main()
