"""Regression cases from the independent core review; temporary fixtures only."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from driftstamp.context import ScanContext
from driftstamp.engine import compare, scan
from driftstamp.migration import plan_bundle
from driftstamp.model import Check, ModuleResult, ModuleSpec, Snapshot, coverage
from driftstamp.modules.scripts import ScriptsModule
from driftstamp.store import Store


class CoreReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home" / "fixture-user"
        self.home.mkdir(parents=True)

    def test_dropping_an_include_does_not_report_existing_script_deleted(self):
        extra = self.home / "unconventional"
        extra.mkdir()
        script = extra / "keep.sh"
        script.write_text("#!/bin/sh\necho hello\n", encoding="utf-8")
        before = scan(
            ScanContext(self.root, self.home, includes=(extra,)),
            modules=[ScriptsModule()], name="before", now="2026-01-01T00:00:00Z",
        )
        after = scan(
            ScanContext(self.root, self.home),
            modules=[ScriptsModule()], name="after", now="2026-01-01T00:00:00Z",
        )
        self.assertTrue(script.exists())
        self.assertEqual(len(before.findings), 1)
        result = compare(before, after)
        self.assertEqual(result["removed"], [])
        self.assertEqual(result["not_observed"], [before.findings[0].id])

    def test_missing_bundle_source_is_a_validation_error(self):
        bundle = self.root / "bundle"
        bundle.mkdir()
        (bundle / "manifest.json").write_text(
            json.dumps({"schema_version": 1, "files": [], "findings": []}),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            plan_bundle(bundle, "ubuntu")

    def test_invalid_bundle_dependencies_are_validation_errors(self):
        bundle = self.root / "bundle"
        bundle.mkdir()
        for finding in ({"resources": []}, {"resources": [], "dependencies": None}):
            with self.subTest(finding=finding):
                (bundle / "manifest.json").write_text(
                    json.dumps({
                        "schema_version": 1,
                        "source": {"platform": "linux"},
                        "files": [],
                        "findings": [finding],
                    }),
                    encoding="utf-8",
                )
                with self.assertRaises(ValueError):
                    plan_bundle(bundle, "ubuntu")

    def test_invalid_plugin_result_does_not_leave_duplicate_checks(self):
        class BrokenModule:
            spec = ModuleSpec(
                "broken", "Broken module", ("linux",),
                (("broken.check", "Inspect something"),),
            )

            def scan(self, ctx):
                return ModuleResult(
                    checks=[Check("broken.check", "complete", "Done")],
                    warnings=None,
                )

        snapshot = scan(
            ScanContext(self.root, self.home), modules=[BrokenModule()],
            name="broken-plugin", now="2026-01-01T00:00:00Z",
        )
        checks = [check for check in snapshot.checks if check.id == "broken.check"]
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].status, "blocked")
        coverage(snapshot.checks)

    def test_failed_snapshot_write_can_be_retried(self):
        store = Store(self.root / "state")
        snapshot = Snapshot(
            "retry", "2026-01-01T00:00:00Z", "linux", "ubuntu", str(self.home),
            [], [], [],
        )

        def fail_during_write(data, stream, **kwargs):
            stream.write("{")
            raise OSError("simulated disk write failure")

        with patch("driftstamp.store.json.dump", side_effect=fail_during_write):
            with self.assertRaises(OSError):
                store.save(snapshot)
        store.save(snapshot)
        self.assertEqual(store.load("retry").id, "retry")


if __name__ == "__main__":
    unittest.main()
