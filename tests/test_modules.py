"""Deterministic discovery fixtures; never inspect or execute host configuration."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from driftstamp.context import ScanContext
from driftstamp.modules import builtin_modules
from driftstamp.modules.cron import CronModule
from driftstamp.modules.i3 import I3Module
from driftstamp.modules.scripts import ScriptsModule
from driftstamp.modules.shell import ShellModule
from driftstamp.modules.systemd import SystemdModule


class ModuleFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home/alex"
        self.home.mkdir(parents=True)
        self.ctx = ScanContext(root=self.root, home=self.home)

    def write(self, relative, content, user=True):
        path = (self.home if user else self.root) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    @staticmethod
    def status(result, check):
        return next(item.status for item in result.checks if item.id == check)

    def test_low_battery_timer_groups_service_dropin_and_script(self):
        self.write(".config/systemd/user/low-battery.timer", "[Timer]\nOnBootSec=2m\nUnit=low-battery.service\n")
        self.write(".config/systemd/user/low-battery.service", "[Service]\nExecStart=/usr/local/bin/low-battery\n")
        self.write(".config/systemd/user/low-battery.service.d/retry.conf", "[Service]\nRestart=on-failure\n")
        self.write("usr/local/bin/low-battery", "#!/bin/sh\nnotify-send 'Battery low'\n", user=False)
        result = SystemdModule().scan(self.ctx)
        timer = next(f for f in result.findings if f.title.endswith("low-battery.timer"))
        self.assertEqual({r.uri for r in timer.resources}, {
            "home://.config/systemd/user/low-battery.timer",
            "home://.config/systemd/user/low-battery.service",
            "home://.config/systemd/user/low-battery.service.d/retry.conf",
            "system://usr/local/bin/low-battery",
        })
        self.assertIn("sh", timer.dependencies)
        self.assertNotIn("low-battery", timer.dependencies)
        self.assertEqual(self.status(result, "systemd.references"), "complete")
        self.assertTrue(any("Timer activates" in e for e in timer.evidence))

    def test_systemd_does_not_export_vendor_executable_or_output_argument(self):
        self.write(".config/systemd/user/message.service", "[Service]\nExecStart=/usr/bin/notify-send /tmp/output\n")
        result = SystemdModule().scan(self.ctx)
        finding = result.findings[0]
        self.assertEqual(len(finding.resources), 1)
        self.assertEqual(finding.dependencies, ["notify-send"])
        self.assertEqual(self.status(result, "systemd.references"), "complete")

    def test_unresolved_systemd_environment_marks_reference_check_partial(self):
        self.write(".config/systemd/user/env.service", "[Service]\nEnvironmentFile=/etc/my.env\nExecStart=/bin/sh -c 'echo hello'\n")
        result = SystemdModule().scan(self.ctx)
        self.assertEqual(self.status(result, "systemd.references"), "partial")
        self.assertEqual(len(result.findings[0].resources), 1)

    def test_i3_literal_include_cycle_and_home_script(self):
        self.write(".config/i3/config", "include extra.conf\nexec --no-startup-id ~/.local/bin/display\n")
        self.write(".config/i3/extra.conf", "include config\nbindsym $mod+d exec dmenu_run\n")
        self.write(".local/bin/display", "#!/bin/sh\nxrandr --auto\n")
        result = I3Module().scan(self.ctx)
        finding = result.findings[0]
        self.assertEqual({r.uri for r in finding.resources}, {
            "home://.config/i3/config", "home://.config/i3/extra.conf", "home://.local/bin/display",
        })
        self.assertIn("dmenu_run", finding.dependencies)
        self.assertIn("sh", finding.dependencies)
        self.assertEqual(self.status(result, "i3.references"), "complete")

    def test_i3_dynamic_expression_is_not_executed(self):
        sentinel = self.root / "never-created"
        self.write(".config/i3/config", f"exec $(touch {sentinel})\ninclude *.conf\n")
        result = I3Module().scan(self.ctx)
        self.assertEqual(self.status(result, "i3.references"), "partial")
        self.assertFalse(sentinel.exists())
        self.assertEqual(len(result.findings[0].resources), 1)

    def test_shell_literal_sources_followed_comments_ignored(self):
        self.write(".bashrc", '# source /not-a-file\nsource "$HOME/.config/aliases"\n')
        self.write(".config/aliases", "alias ll='ls -l'\n")
        result = ShellModule().scan(self.ctx)
        finding = result.findings[0]
        self.assertEqual({r.uri for r in finding.resources}, {"home://.bashrc", "home://.config/aliases"})
        self.assertEqual(self.status(result, "shell.sources"), "complete")

    def test_shell_relative_and_conditional_source_remain_unresolved(self):
        self.write(".bashrc", "source aliases\n[ -f ~/.extra ] && . ~/.extra\n")
        self.write("aliases", "secret file not necessarily sourced")
        result = ShellModule().scan(self.ctx)
        self.assertEqual(self.status(result, "shell.sources"), "partial")
        self.assertEqual(len(result.findings[0].resources), 1)

    def test_scripts_skip_binaries_readmes_and_dependency_trees(self):
        self.write(".local/bin/backup", "#!/bin/sh\necho backed-up\n")
        self.write(".local/bin/compiled", b"\x7fELF\x00\xff\x00")
        self.write("scripts/README.md", "How scripts work")
        self.write("scripts/node_modules/dependency/index.py", "print('third-party')")
        self.write("scripts/my-task.py", "print('local')")
        result = ScriptsModule().scan(self.ctx)
        uris = {r.uri for f in result.findings for r in f.resources}
        self.assertEqual(uris, {"home://.local/bin/backup", "home://scripts/my-task.py"})
        self.assertEqual(self.status(result, "scripts.user"), "complete")

    def test_explicit_script_directory_is_included(self):
        custom = self.write("opt/my-tools/hello.py", "print('hi')", user=False)
        ctx = ScanContext(root=self.root, home=self.home, includes=(custom.parent,))
        result = ScriptsModule().scan(ctx)
        self.assertEqual(result.findings[0].resources[0].uri, "system://opt/my-tools/hello.py")

    def test_cron_preserves_command_and_follows_only_script(self):
        self.write("etc/cron.d/backup", "SHELL=/bin/sh\n0 2 * * * alex /home/alex/bin/backup /tmp/output\n", user=False)
        self.write("bin/backup", "#!/bin/sh\necho backup\n")
        result = CronModule().scan(self.ctx)
        self.assertEqual(len(result.findings), 1)
        self.assertEqual({r.uri for r in result.findings[0].resources}, {"system://etc/cron.d/backup", "home://bin/backup"})
        self.assertEqual(self.status(result, "cron.references"), "complete")

    def test_cron_user_macros_and_malformed_entries(self):
        self.write("var/spool/cron/crontabs/alex", "@reboot notify-send hello\ninvalid entry\n", user=False)
        result = CronModule().scan(self.ctx)
        self.assertIn("notify-send", result.findings[0].dependencies)
        self.assertEqual(self.status(result, "cron.references"), "partial")

    def test_excluded_config_not_read_and_not_reported_complete(self):
        path = self.write(".config/i3/config", "exec dmenu_run\n")
        ctx = ScanContext(root=self.root, home=self.home, excludes=(str(path),))
        result = I3Module().scan(ctx)
        self.assertEqual(result.findings, [])
        self.assertEqual(self.status(result, "i3.user"), "excluded")

    def test_permission_denial_reported_without_mode_bit_assumptions(self):
        path = self.write(".bashrc", "alias hi=hello\n")
        original = ScanContext.read_text

        def read(ctx, candidate):
            if candidate == path:
                raise PermissionError("fixture denied")
            return original(ctx, candidate)

        with patch.object(ScanContext, "read_text", read):
            result = ShellModule().scan(self.ctx)
        self.assertEqual(self.status(result, "shell.user"), "blocked")
        self.assertEqual(result.findings, [])

    def test_scope_and_check_catalog_match(self):
        ctx = ScanContext(root=self.root, home=self.home, scope="system")
        for module in builtin_modules():
            with self.subTest(module=module.spec.id):
                result = module.scan(ctx)
                self.assertEqual({c.id for c in result.checks}, {key for key, _ in module.spec.checks})
                self.assertEqual(self.status(result, module.spec.id + ".user"), "excluded")

    def test_finding_identity_survives_content_changes(self):
        path = self.write(".bashrc", "alias ll='ls -l'\n")
        before = ShellModule().scan(self.ctx).findings[0]
        path.write_text("alias ll='ls -la'\n", encoding="utf-8")
        after = ShellModule().scan(self.ctx).findings[0]
        self.assertEqual(before.id, after.id)
        self.assertNotEqual(before.resources[0].sha256, after.resources[0].sha256)


if __name__ == "__main__":
    unittest.main()
