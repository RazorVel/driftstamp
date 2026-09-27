"""Installer refuses unrelated files before creating or changing environments."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

spec = importlib.util.spec_from_file_location("install_user", Path(__file__).resolve().parents[1] / "tools/install_user.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class UserInstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.prefix = self.root / "user prefix"
        self.wheel = self.root / "driftstamp-0.1.2-py3-none-any.whl"

    def make_wheel(self, name="driftstamp", manual=True):
        with ZipFile(self.wheel, "w") as archive:
            archive.writestr("driftstamp-0.1.2.dist-info/METADATA", f"Name: {name}\nVersion: 0.1.2\n")
            if manual:
                archive.writestr("driftstamp-0.1.2.data/data/share/man/man1/driftstamp.1", ".TH DRIFTSTAMP 1\n")

    def symlink(self, path, target):
        try:
            path.symlink_to(target, target_is_directory=target.is_dir())
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable")

    @unittest.skipUnless(os.name == "posix", "User installer requires POSIX")
    def test_wrong_distribution_does_not_touch_prefix(self):
        self.make_wheel(name="unrelated")
        with self.assertRaisesRegex(ValueError, "not Driftstamp"):
            installer.install(self.wheel, self.prefix)
        self.assertFalse(self.prefix.exists())

    def test_non_posix_install_rejected_before_touching_prefix(self):
        with patch.object(installer.os, "name", "nt"):
            with self.assertRaisesRegex(ValueError, "supports POSIX"):
                installer.install(self.wheel, self.prefix)
        self.assertFalse(self.prefix.exists())

    def test_missing_manual_rejected(self):
        self.make_wheel(manual=False)
        with self.assertRaisesRegex(ValueError, "manual"):
            installer.wheel_manual(self.wheel)

    def test_foreign_command_preserved(self):
        command = self.prefix / "bin/driftstamp"
        command.parent.mkdir(parents=True)
        command.write_text("existing command")
        with self.assertRaisesRegex(ValueError, "unrelated command"):
            installer.preflight(self.prefix)
        self.assertEqual(command.read_text(), "existing command")

    def test_broken_foreign_symlink_preserved(self):
        command = self.prefix / "bin/driftstamp"
        command.parent.mkdir(parents=True)
        self.symlink(command, self.root / "missing")
        with self.assertRaisesRegex(ValueError, "unrelated command"):
            installer.preflight(self.prefix)
        self.assertTrue(command.is_symlink())

    def test_unrecognized_environment_rejected(self):
        location, _, _ = installer.installation_paths(self.prefix)
        location.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "unrecognized"):
            installer.preflight(self.prefix)

    def test_recognized_install_can_be_updated_with_spaces_in_prefix(self):
        location, command, manual = installer.installation_paths(self.prefix)
        location.mkdir(parents=True)
        (location / "install.json").write_text(json.dumps(installer.MARKER))
        command.parent.mkdir(parents=True)
        self.symlink(command, location / "environments/env-old/bin/driftstamp")
        self.assertEqual(installer.preflight(self.prefix), (location, command, manual))

    @unittest.skipUnless(os.name == "posix", "User installer requires POSIX")
    def test_failure_before_verification_does_not_publish_command_or_manual(self):
        self.make_wheel()
        location, command, manual = installer.installation_paths(self.prefix)
        manual.parent.mkdir(parents=True)
        manual.write_text("old manual")
        with patch.object(installer.venv.EnvBuilder, "create"), \
                patch.object(installer.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "pip")):
            with self.assertRaises(subprocess.CalledProcessError):
                installer.install(self.wheel, self.prefix)
        self.assertFalse(command.exists())
        self.assertEqual(manual.read_text(), "old manual")
        self.assertEqual(list((location / "environments").iterdir()), [])
        installer.preflight(self.prefix)  # A failed new install remains retryable.

    def existing_install(self):
        location, command, manual = installer.installation_paths(self.prefix)
        executable = location / "environments/env-old/bin/driftstamp"
        executable.parent.mkdir(parents=True)
        executable.write_text("working old executable")
        (location / "install.json").write_text(json.dumps(installer.MARKER))
        command.parent.mkdir(parents=True)
        self.symlink(command, executable)
        manual.parent.mkdir(parents=True)
        manual.write_text("old manual")
        return location, command, manual, executable

    @staticmethod
    def fake_environment(environment):
        executable = environment / "bin/driftstamp"
        executable.parent.mkdir()
        executable.write_text("new executable")

    @unittest.skipUnless(os.name == "posix", "User installer requires POSIX")
    def test_failed_upgrade_verification_preserves_working_command_and_manual(self):
        self.make_wheel()
        location, command, manual, old = self.existing_install()
        with patch.object(installer.venv.EnvBuilder, "create", side_effect=self.fake_environment), \
                patch.object(installer.subprocess, "run", side_effect=[None, subprocess.CalledProcessError(1, "driftstamp")]) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                installer.install(self.wheel, self.prefix)
        attempted = Path(run.call_args_list[1].args[0][0])
        self.assertNotEqual(attempted, old)
        self.assertFalse(attempted.parent.parent.exists())
        self.assertEqual(command.readlink(), old)
        self.assertEqual(command.read_text(), "working old executable")
        self.assertEqual(manual.read_text(), "old manual")
        self.assertEqual(list((location / "environments").iterdir()), [old.parent.parent])
        installer.preflight(self.prefix)

    @unittest.skipUnless(os.name == "posix", "User installer requires POSIX")
    def test_successful_upgrade_switches_command_and_retains_previous_environment(self):
        self.make_wheel()
        location, command, manual, old = self.existing_install()
        with patch.object(installer.venv.EnvBuilder, "create", side_effect=self.fake_environment), \
                patch.object(installer.subprocess, "run") as run, patch("builtins.print"):
            installer.install(self.wheel, self.prefix)
        verified = Path(run.call_args_list[1].args[0][0])
        self.assertEqual(command.readlink(), verified)
        self.assertNotEqual(verified, old)
        self.assertEqual(command.read_text(), "new executable")
        self.assertEqual(old.read_text(), "working old executable")
        self.assertEqual(manual.read_text(), ".TH DRIFTSTAMP 1\n")
        self.assertEqual(len(list((location / "environments").iterdir())), 2)
        installer.preflight(self.prefix)

    def test_link_into_environment_needs_installation_marker(self):
        location, command, _ = installer.installation_paths(self.prefix)
        command.parent.mkdir(parents=True)
        self.symlink(command, location / "environments/env-forged/bin/driftstamp")
        with self.assertRaisesRegex(ValueError, "unrelated command"):
            installer.preflight(self.prefix)

    def test_environment_directory_symlink_cannot_masquerade_as_owned_command(self):
        location, command, _, old = self.existing_install()
        alias = location / "environments/env-alias"
        self.symlink(alias, old.parent.parent)
        command.unlink()
        self.symlink(command, alias / "bin/driftstamp")
        with self.assertRaisesRegex(ValueError, "unrelated command"):
            installer.preflight(self.prefix)


if __name__ == "__main__":
    unittest.main()
