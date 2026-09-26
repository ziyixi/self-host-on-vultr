"""Offline Mail Hero Compose contract; never read credentials or contact Docker."""

from pathlib import Path
import subprocess
import unittest

from test_newsletter_deployment import load_compose_config

ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER = "ghcr.io/ziyixi/mail-hero-backup@sha256:" + "0" * 64


class MailHeroBackupDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Shared parser rejects old Compose before it can resolve private env.
        cls.config = load_compose_config()
        cls.service = cls.config["services"]["mailhero-backup"]

    def test_image_is_immutable_and_runtime_is_not_built_on_server(self):
        self.assertRegex(self.service["image"], r"^ghcr\.io/ziyixi/mail-hero-backup@sha256:[0-9a-f]{64}$")
        self.assertFalse(self.service.get("build"))
        self.assertEqual(self.service["platform"], "linux/amd64")
        self.assertEqual(self.service["command"], ["run"])
        self.assertFalse(self.service.get("entrypoint"), "Use the tested image's entrypoint")
        self.assertNotEqual(self.service["image"], PLACEHOLDER, "Replace the placeholder with a verified CI digest before deployment")

    def test_scheduler_is_non_root_and_resource_bounded(self):
        service = self.service
        self.assertEqual(service["user"], "1000:1000")
        self.assertTrue(service["init"])
        self.assertEqual(service["restart"], "unless-stopped")
        self.assertTrue(service["read_only"])
        self.assertEqual(service["cap_drop"], ["ALL"])
        self.assertIn("no-new-privileges:true", service["security_opt"])
        self.assertFalse(service.get("privileged", False))
        self.assertFalse(service.get("cap_add"))
        self.assertEqual(int(service["mem_limit"]), 512 * 1024 * 1024)
        self.assertEqual(float(service["cpus"]), 1)
        self.assertEqual(service["pids_limit"], 64)
        self.assertEqual(service["stop_grace_period"], "4m0s")
        self.assertEqual(service["tmpfs"], ["/tmp:rw,nosuid,nodev,size=128m,mode=1777"])

    def test_only_existing_private_config_and_state_are_mounted(self):
        mounts = self.service["volumes"]
        self.assertEqual(len(mounts), 2)
        by_target = {mount["target"]: mount for mount in mounts}
        config = by_target["/run/mailhero-backup"]
        state = by_target["/var/lib/mailhero-backup"]
        self.assertEqual(config["source"], "/home/xiziyi/.config/mail-hero-backup")
        self.assertTrue(config["read_only"])
        self.assertEqual(state["source"], "/home/xiziyi/mail-hero-backup")
        self.assertFalse(state.get("read_only", False))
        for mount in mounts:
            self.assertEqual(mount["type"], "bind")
            self.assertFalse(mount["bind"]["create_host_path"])
            self.assertNotIn("docker.sock", mount["source"])
        existing_backup_sources = {mount["source"] for mount in self.config["services"]["backup"]["volumes"]}
        self.assertNotIn(config["source"], existing_backup_sources)
        self.assertNotIn(state["source"], existing_backup_sources)

    def test_credentials_are_files_and_never_compose_environment(self):
        self.assertFalse(self.service.get("env_file"))
        self.assertEqual(self.service["environment"], {"BACKUP_AT_UTC": "04:17"})
        example = (ROOT / "mailhero-backup/credentials.env.example").read_text()
        values = dict(line.split("=", 1) for line in example.splitlines()
                      if line and not line.startswith("#"))
        self.assertEqual(set(values), {"MAIL_HERO_ORIGIN", "BACKUP_RECIPIENT", "BACKUP_TOKEN",
                                      "BACKUP_RECEIPT_KEY", "CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET"})
        for name, value in values.items():
            if name != "MAIL_HERO_ORIGIN":
                self.assertTrue(value.startswith("REPLACE_WITH_"))
        ignored = subprocess.run(["git", "check-ignore", "--stdin"], cwd=ROOT,
                                 input="mailhero-backup/credentials.env\n", text=True, capture_output=True)
        self.assertEqual(ignored.returncode, 0)
        tracked_example = subprocess.run(["git", "check-ignore", "--stdin"], cwd=ROOT,
                                         input="mailhero-backup/credentials.env.example\n", text=True, capture_output=True)
        self.assertEqual(tracked_example.returncode, 1)

    def test_outbound_bridge_is_separate_and_has_no_inbound_ports(self):
        self.assertEqual(set(self.service["networks"]), {"mailhero-backup"})
        network = self.config["networks"]["mailhero-backup"]
        self.assertFalse(network.get("internal", False), "Collector requires outbound HTTPS")
        self.assertIn(network.get("driver", "bridge"), ("bridge", ""))
        self.assertFalse(self.service.get("ports"))
        self.assertFalse(self.service.get("expose"))
        self.assertFalse(self.service.get("depends_on"))
        self.assertFalse(self.service.get("network_mode"))
        self.assertFalse(self.service.get("pid"))
        for name, service in self.config["services"].items():
            if name != "mailhero-backup":
                self.assertNotIn("mailhero-backup", service.get("networks", {}), name)

    def test_health_and_logs_are_bounded_and_use_the_image_runtime(self):
        health = self.service["healthcheck"]
        self.assertEqual(health["test"], ["CMD", "python3", "-I", "/app/container.py", "health"])
        self.assertEqual(health["interval"], "1m0s")
        self.assertEqual(health["timeout"], "10s")
        self.assertEqual(health["start_period"], "1m30s")
        self.assertEqual(health["retries"], 3)
        self.assertEqual(self.service["logging"], {"driver": "json-file", "options": {"max-size": "5m", "max-file": "3"}})

    def test_host_scripts_are_retired_and_docs_only_manage_this_service(self):
        for name in ("install.sh", "run.sh", "upgrade-guard.sh", "mailhero-backup.service", "mailhero-backup.timer"):
            self.assertFalse((ROOT / "mailhero-backup" / name).exists())
        docs = (ROOT / "mailhero-backup/README.md").read_text()
        for command in ("docker compose pull mailhero-backup", "docker compose up -d --no-deps mailhero-backup",
                        "docker compose run --rm --no-deps mailhero-backup once",
                        "docker compose exec -T mailhero-backup python3 -I /app/container.py health",
                        "docker compose stop mailhero-backup"):
            self.assertIn(command, docs)
        self.assertIn("04:17 UTC", docs)
        self.assertIn("7 distinct days and 4 distinct ISO weeks", docs)
        self.assertIn("4 minutes (240 seconds)", docs)
        self.assertNotIn("sudo systemctl", docs)
        self.assertIn("private recovery key stays on the trusted recovery device", docs)


if __name__ == "__main__":
    unittest.main()
