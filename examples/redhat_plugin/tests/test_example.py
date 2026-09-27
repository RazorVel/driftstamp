"""Source-only tests: no installation, RPM database, network, or privileges."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT))
sys.path.insert(0, str(EXAMPLE_ROOT))

from driftstamp.context import ScanContext
from driftstamp.model import coverage
from driftstamp_redhat_example import RedHatExample


class RedHatExampleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home" / "fixture-user"
        self.home.mkdir(parents=True)

    def context(self, **kwargs):
        defaults = {
            "root": self.root,
            "home": self.home,
            "platform": "linux",
            "distro": "rhel",
            "scope": "all",
        }
        defaults.update(kwargs)
        return ScanContext(**defaults)

    def test_spec_declares_version_and_unique_prefixed_check(self):
        spec = RedHatExample.spec
        self.assertEqual(spec.api_version, 1)
        self.assertEqual(spec.platforms, ("linux",))
        self.assertEqual(len(spec.checks), 1)
        self.assertTrue(spec.checks[0][0].startswith(spec.id + "."))

    def test_example_reports_unsupported_without_reading_machine(self):
        with (
            patch.object(ScanContext, "read_text", side_effect=AssertionError("unexpected read")),
            patch.object(ScanContext, "walk_files", side_effect=AssertionError("unexpected walk")),
            patch.object(ScanContext, "resource", side_effect=AssertionError("unexpected resource")),
        ):
            result = RedHatExample().scan(self.context())
        self.assertEqual(result.findings, [])
        self.assertEqual([check.status for check in result.checks], ["unsupported"])
        self.assertIn("not implemented", result.checks[0].detail)
        self.assertTrue(result.warnings)

    def test_unsupported_check_does_not_receive_inspection_credit(self):
        result = RedHatExample().scan(self.context())
        report = coverage(result.checks)
        self.assertEqual(report["percent"], 0.0)
        self.assertEqual(report["completed"], 0)
        self.assertEqual(report["total"], 1)

    def test_user_scope_remains_visible_as_excluded(self):
        result = RedHatExample().scan(self.context(scope="user"))
        self.assertEqual(result.findings, [])
        self.assertEqual([check.status for check in result.checks], ["excluded"])
        self.assertEqual(coverage(result.checks)["total"], 1)

    def test_direct_windows_call_does_not_claim_support(self):
        result = RedHatExample().scan(self.context(platform="windows", distro="windows"))
        self.assertEqual(result.findings, [])
        self.assertEqual([check.status for check in result.checks], ["unsupported"])


if __name__ == "__main__":
    unittest.main()
