"""Collector edge cases use isolated files; no host services or commands run."""
import tempfile
import unittest
from pathlib import Path

from driftstamp.context import ScanContext
from driftstamp.modules.common import interpreter_dependency
from driftstamp.modules.cron import CronModule
from driftstamp.modules.i3 import I3Module
from driftstamp.modules.scripts import ScriptsModule
from driftstamp.modules.shell import ShellModule
from driftstamp.modules.systemd import SystemdModule


class CollectorEdgeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home/alex"
        self.home.mkdir(parents=True)
        self.ctx = ScanContext(self.root, self.home)

    def write(self, relative, text):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def status(self, result, key):
        return next(check.status for check in result.checks if check.id == key)

    def uris(self, finding):
        return {resource.uri for resource in finding.resources}

    def test_i3_preserves_spaces_in_quoted_commands(self):
        self.write("bin/my  script", "#!/bin/sh\necho example\n")
        for prefix in ("exec", "exec_always --no-startup-id", "bindsym --release $mod+x exec", "bindcode 24 exec", "status_command", "i3bar_command"):
            with self.subTest(prefix=prefix):
                self.write(".config/i3/config", f'{prefix} "$HOME/bin/my  script"\n')
                result = I3Module().scan(self.ctx)
                self.assertIn("home://bin/my  script", self.uris(result.findings[0]))
                self.assertEqual(self.status(result, "i3.references"), "complete")

    def test_i3_interpreter_literal_home_argument_is_resolved(self):
        self.write("bin/task.py", "print('hello')\n")
        self.write(".config/i3/config", 'exec python3 -u "${HOME}/bin/task.py"\n')
        result = I3Module().scan(self.ctx)
        self.assertIn("home://bin/task.py", self.uris(result.findings[0]))
        self.assertEqual(result.findings[0].dependencies, ["python3"])
        self.assertEqual(self.status(result, "i3.references"), "complete")

    def test_i3_malformed_include_and_command_are_partial(self):
        self.write(".config/i3/config", 'include "unclosed\nexec "unclosed\n')
        result = I3Module().scan(self.ctx)
        self.assertEqual(self.status(result, "i3.references"), "partial")
        self.assertEqual(self.uris(result.findings[0]), {"home://.config/i3/config"})

    def test_i3_shell_expressions_never_execute(self):
        sentinel = self.root / "not-created"
        self.write(".config/i3/config", f"exec sh -c 'touch {sentinel}'\n")
        result = I3Module().scan(self.ctx)
        self.assertEqual(self.status(result, "i3.references"), "partial")
        self.assertFalse(sentinel.exists())

    def test_env_shebang_options_and_assignments_are_not_dependencies(self):
        cases = {
            "#!/usr/bin/env -S MODE=test python3 -u": ["python3"],
            "#!/usr/bin/env -S 'python3 -u'": ["python3"],
            "#!/usr/bin/env --split-string='MODE=test python3 -u'": ["python3"],
            "#!/usr/bin/env -u PYTHONPATH -C /tmp python3": ["python3"],
            "#!/usr/bin/env --unset=PYTHONPATH --chdir=/tmp bash": ["bash"],
            "#!/usr/bin/env -i MODE=test python3": ["python3"],
            "#!/usr/bin/env MODE=test": [],
            "#!/usr/bin/env -S": [],
            "#!/usr/bin/env --unknown python3": [],
            "#!/usr/bin/env -u": [],
            "#!/usr/bin/env $CUSTOM_INTERPRETER": [],
            "#!/usr/bin/env 'unterminated": [],
            "#!": [],
            "#!/": [],
        }
        for shebang, expected in cases.items():
            with self.subTest(shebang=shebang):
                self.assertEqual(interpreter_dependency(shebang + "\n"), expected)

    def test_script_finding_uses_env_interpreter(self):
        self.write("bin/worker", "#!/usr/bin/env -S MODE=test python3 -u\nprint('hello')\n")
        result = ScriptsModule().scan(self.ctx)
        self.assertEqual(result.findings[0].dependencies, ["python3"])

    def test_repeated_explicit_script_root_does_not_duplicate_findings(self):
        script = self.write("bin/task", "#!/bin/sh\n")
        result = ScriptsModule().scan(ScanContext(self.root, self.home, includes=(script, script.parent)))
        self.assertEqual(len(result.findings), 1)

    def test_non_utf8_script_candidate_is_ignored(self):
        path = self.write("bin/compiled.py", "")
        path.write_bytes(b"\xff\xfe")
        result = ScriptsModule().scan(self.ctx)
        self.assertEqual(result.findings, [])
        self.assertEqual(self.status(result, "scripts.user"), "complete")

    def test_shell_source_cycle_and_quoted_spaces_remain_bounded(self):
        self.write(".bashrc", 'source "$HOME/.config/my  aliases"\n')
        self.write(".config/my  aliases", 'source "$HOME/.bashrc"\n')
        result = ShellModule().scan(self.ctx)
        self.assertEqual(self.uris(result.findings[0]), {"home://.bashrc", "home://.config/my  aliases"})
        self.assertEqual(self.status(result, "shell.sources"), "complete")

    def test_shell_malformed_and_dynamic_sources_are_partial(self):
        self.write(".bashrc", 'source "unterminated\nsource "$OTHER/file"\nsource ~/.config/*.sh\n')
        result = ShellModule().scan(self.ctx)
        self.assertEqual(self.status(result, "shell.sources"), "partial")
        self.assertEqual(self.uris(result.findings[0]), {"home://.bashrc"})

    def test_shell_include_limit_is_reported(self):
        self.write(".bashrc", 'source "$HOME/chain/0"\n')
        for index in range(70):
            self.write(f"chain/{index}", f'source "$HOME/chain/{index + 1}"\n')
        result = ShellModule().scan(self.ctx)
        self.assertEqual(self.status(result, "shell.sources"), "partial")
        self.assertIn("Source traversal limit", next(check.detail for check in result.checks if check.id == "shell.sources"))

    def test_cron_quotes_preserve_script_whitespace(self):
        self.write("bin/my  task", "#!/bin/sh\n")
        spool = self.root / "var/spool/cron/alex"
        spool.parent.mkdir(parents=True)
        spool.write_text('@reboot "$HOME/bin/my  task"\n', encoding="utf-8")
        result = CronModule().scan(self.ctx)
        self.assertIn("home://bin/my  task", self.uris(result.findings[0]))
        self.assertEqual(self.status(result, "cron.references"), "complete")

    def test_unknown_cron_macro_is_partial_and_not_a_launch_reference(self):
        spool = self.root / "var/spool/cron/alex"
        spool.parent.mkdir(parents=True)
        spool.write_text("@someday imaginary-program\n", encoding="utf-8")
        result = CronModule().scan(self.ctx)
        self.assertEqual(self.status(result, "cron.references"), "partial")
        self.assertEqual(result.findings[0].dependencies, [])

    def test_timer_dropin_overrides_target_before_following_service(self):
        prefix = ".config/systemd/user/"
        self.write(prefix + "backup.timer", "[Timer]\nUnit=old.service\n")
        self.write(prefix + "backup.timer.d/10-target.conf", "[Timer]\nUnit = intermediate.service\n")
        self.write(prefix + "backup.timer.d/20-target.conf", "[Timer]\nUnit = new.service\n")
        self.write(prefix + "old.service", "[Service]\nExecStart=/usr/bin/old-program\n")
        self.write(prefix + "new.service", "[Service]\nExecStart=/usr/bin/new-program\n")
        result = SystemdModule().scan(self.ctx)
        timer = next(f for f in result.findings if f.title == "Systemd: backup.timer")
        self.assertIn("home://" + prefix + "new.service", self.uris(timer))
        self.assertNotIn("home://" + prefix + "old.service", self.uris(timer))
        self.assertEqual(timer.dependencies, ["new-program"])
        self.assertEqual(self.status(result, "systemd.references"), "complete")

    def test_timer_unit_in_wrong_section_does_not_redirect_target(self):
        prefix = ".config/systemd/user/"
        self.write(prefix + "backup.timer", "[Timer]\nUnit=backup.service\n[Install]\nUnit=wrong.service\n")
        self.write(prefix + "backup.service", "[Service]\nExecStart=/usr/bin/true\n")
        result = SystemdModule().scan(self.ctx)
        timer = next(f for f in result.findings if f.title == "Systemd: backup.timer")
        self.assertIn("home://" + prefix + "backup.service", self.uris(timer))
        self.assertEqual(self.status(result, "systemd.references"), "complete")

    def test_dynamic_timer_override_does_not_link_stale_target(self):
        prefix = ".config/systemd/user/"
        self.write(prefix + "backup.timer", "[Timer]\nUnit=old.service\n")
        self.write(prefix + "backup.timer.d/override.conf", "[Timer]\nUnit=%i.service\n")
        self.write(prefix + "old.service", "[Service]\nExecStart=/usr/bin/old-program\n")
        result = SystemdModule().scan(self.ctx)
        timer = next(f for f in result.findings if f.title == "Systemd: backup.timer")
        self.assertNotIn("home://" + prefix + "old.service", self.uris(timer))
        self.assertEqual(self.status(result, "systemd.references"), "partial")

    def test_service_exec_reset_discards_overridden_script_reference(self):
        prefix = ".config/systemd/user/"
        self.write("bin/old-script", "#!/bin/sh\n")
        self.write("bin/new-script", "#!/usr/bin/python3\n")
        self.write(prefix + "backup.service", "[Service]\nExecStart=/home/alex/bin/old-script\n")
        self.write(prefix + "backup.service.d/override.conf", "[Service]\nExecStart=\nExecStart=/home/alex/bin/new-script\n")
        result = SystemdModule().scan(self.ctx)
        service = next(f for f in result.findings if f.title == "Systemd: backup.service")
        self.assertNotIn("home://bin/old-script", self.uris(service))
        self.assertIn("home://bin/new-script", self.uris(service))
        self.assertEqual(service.dependencies, ["python3"])
        self.assertEqual(self.status(result, "systemd.references"), "complete")

    def test_unit_execution_context_is_marked_partial(self):
        for setting in ("Environment=MODE=test", "RootImage=/custom.img", "ExecSearchPath=/custom/bin"):
            with self.subTest(setting=setting):
                self.write(".config/systemd/user/task.service", f"[Service]\n{setting}\nExecStart=/usr/bin/true\n")
                result = SystemdModule().scan(self.ctx)
                self.assertEqual(self.status(result, "systemd.references"), "partial")

    def test_orphan_dropin_preserved_with_incomplete_reference_check(self):
        self.write(".config/systemd/user/vendor.service.d/override.conf", "[Service]\nExecStart=/usr/bin/example\n")
        result = SystemdModule().scan(self.ctx)
        self.assertEqual(self.status(result, "systemd.references"), "partial")
        self.assertEqual(self.uris(result.findings[0]), {"home://.config/systemd/user/vendor.service.d/override.conf"})

    def test_inherited_template_dropin_does_not_claim_complete_references(self):
        prefix = ".config/systemd/user/"
        self.write(prefix + "worker@.service", "[Service]\nExecStart=/usr/bin/original\n")
        self.write(prefix + "worker@one.service", "[Service]\nExecStart=/usr/bin/original\n")
        self.write(prefix + "worker@.service.d/override.conf", "[Service]\nExecStart=\nExecStart=/usr/bin/replacement\n")
        result = SystemdModule().scan(self.ctx)
        self.assertEqual(self.status(result, "systemd.references"), "partial")
        self.assertTrue(any("Inherited drop-in" in check.detail for check in result.checks))

    def test_multiline_unit_command_does_not_become_bogus_dependency(self):
        self.write(".config/systemd/user/task.service", "[Service]\nExecStart=/usr/bin/program \\\n  argument\n")
        result = SystemdModule().scan(self.ctx)
        self.assertEqual(self.status(result, "systemd.references"), "partial")
        self.assertEqual(result.findings[0].dependencies, [])


if __name__ == "__main__":
    unittest.main()
