"""Engine contracts exercised with deterministic, host-independent plugin fixtures."""
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from driftstamp.context import ScanContext
from driftstamp.engine import compare, scan, snapshot_from_dict
from driftstamp.model import Check, Finding, ModuleResult, ModuleSpec, Resource, coverage
from driftstamp.registry import load_modules, module_catalog, validate_module


class FixtureModule:
    spec = ModuleSpec("fixture", "Fixture module", ("linux", "windows"),
                      (("fixture.files", "Inspect fixture files"),))

    def __init__(self):
        self.result = ModuleResult(
            [Finding("fixture-one", "fixture", "A custom setting", "Local fixture", [], [], 75)],
            [Check("fixture.files", "complete", "Inspected")],
        )

    def scan(self, ctx):
        return self.result


class Entry:
    def __init__(self, name, factory):
        self.name, self.factory = name, factory

    def load(self):
        return self.factory


class EngineEdgeTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / "home" / "fixture"
        self.home.mkdir(parents=True)
        self.ctx = ScanContext(self.root, self.home, distro="ubuntu")

    def snapshot(self, module=None, name="fixture", **kwargs):
        return scan(self.ctx, modules=[module or FixtureModule()], name=name,
                    now="2026-01-01T00:00:00Z", **kwargs)

    def test_platform_or_distribution_change_cannot_establish_deletion(self):
        before = self.snapshot()
        for changes in ({"platform": "windows"}, {"distro": "arch"}):
            with self.subTest(changes=changes):
                after = replace(before, id="after", findings=[], **changes)
                result = compare(before, after)
                self.assertEqual(result["removed"], [])
                self.assertEqual(result["not_observed"], ["fixture-one"])

    def test_duplicate_scope_options_do_not_create_false_scope_changes(self):
        self.ctx.includes = (self.home,)
        self.ctx.excludes = ("*.tmp",)
        before = self.snapshot(selected=["fixture"])
        self.ctx.includes = (self.home, self.home)
        self.ctx.excludes = ("*.tmp", "*.tmp")
        module = FixtureModule()
        module.result.findings = []
        after = self.snapshot(module, "after", selected=["fixture", "fixture"])
        self.assertEqual(before.scope, after.scope)
        self.assertEqual(compare(before, after)["removed"], ["fixture-one"])

    def test_dropped_capability_check_cannot_establish_deletion(self):
        before = self.snapshot()
        before.checks.append(Check("fixture.extra", "complete", "Previously inspected"))
        after = self.snapshot(name="after")
        after.findings = []
        result = compare(before, after)
        self.assertEqual(result["removed"], [])
        self.assertEqual(result["not_observed"], ["fixture-one"])

    def test_reusing_result_after_traversal_gap_does_not_keep_stale_uncertainty(self):
        module = FixtureModule()
        original = deepcopy(module.result)

        def with_gap(ctx):
            ctx.issues.append({"status": "partial", "path": "fixture://unreadable"})
            return module.result

        module.scan = with_gap
        first = self.snapshot(module)
        self.assertEqual(next(c for c in first.checks if c.id == "fixture.files").status, "partial")
        self.assertEqual(module.result, original)
        module.scan = lambda ctx: module.result
        second = self.snapshot(module, "second")
        self.assertEqual(next(c for c in second.checks if c.id == "fixture.files").status, "complete")
        self.assertEqual(second.warnings, [])

    def test_excluded_gap_is_not_counted_as_complete(self):
        module = FixtureModule()

        def with_gap(ctx):
            ctx.issues.append({"status": "excluded", "path": "fixture://excluded"})
            return module.result

        module.scan = with_gap
        result = self.snapshot(module)
        self.assertEqual(next(c for c in result.checks if c.id == "fixture.files").status, "excluded")
        self.assertEqual(coverage(result.checks)["percent"], 0)

    def test_module_name_alone_does_not_satisfy_known_pending_capability(self):
        module = FixtureModule()
        module.spec = ModuleSpec("package-config", "Other metadata", ("linux",),
                                 (("package-config.inventory", "Count packages"),))
        module.result = ModuleResult(checks=[Check("package-config.inventory", "complete", "Counted")])
        result = self.snapshot(module)
        self.assertEqual(next(c for c in result.checks if c.id == "package-config.baselines").status,
                         "unsupported")

    def test_exact_capability_removes_placeholder_without_duplicate_checks(self):
        module = FixtureModule()
        module.spec = ModuleSpec("package-config", "Baselines", ("linux",),
                                 (("package-config.baselines", "Compare baselines"),))
        module.result = ModuleResult(checks=[Check("package-config.baselines", "complete", "Compared")])
        result = self.snapshot(module)
        matching = [c for c in result.checks if c.id == "package-config.baselines"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].status, "complete")
        coverage(result.checks)

    def test_capability_for_another_platform_cannot_hide_pending_gap(self):
        module = FixtureModule()
        module.spec = ModuleSpec("package-config", "Other platform", ("windows",),
                                 (("package-config.baselines", "Compare baselines"),))
        result = self.snapshot(module)
        matching = [c for c in result.checks if c.id == "package-config.baselines"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].status, "unsupported")

    def test_invalid_plugin_records_are_rejected_atomically(self):
        invalid_resources = [
            Resource("home://valid", "/fixture", sha256="broken"),
            Resource("home://../escape", "/fixture"),
            Resource("home://valid", "/fixture", mode=True),
            Resource("home://valid", "/fixture", symlink=[]),
        ]
        cases = [ModuleResult([Finding("bad", "fixture", "Bad", "", [resource], [], 50)],
                              [Check("fixture.files", "complete", "Inspected")])
                 for resource in invalid_resources]
        cases.extend([
            ModuleResult([Finding("bad", "fixture", None, "", [], [], 50)],
                         [Check("fixture.files", "complete", "Inspected")]),
            ModuleResult(checks=[Check("fixture.files", "complete", None)]),
            ModuleResult(checks=[Check("fixture.files", "complete", "Inspected")],
                         warnings=["invalid\x00warning"]),
        ])
        for invalid in cases:
            with self.subTest(result=invalid):
                module = FixtureModule()
                module.result = invalid
                result = self.snapshot(module)
                self.assertEqual(result.findings, [])
                checks = [c for c in result.checks if c.id == "fixture.files"]
                self.assertEqual(len(checks), 1)
                self.assertEqual(checks[0].status, "blocked")
                coverage(result.checks)

    def test_duplicate_finding_ids_cannot_publish_partial_plugin_output(self):
        module = FixtureModule()
        module.result.findings *= 2
        result = self.snapshot(module)
        self.assertEqual(result.findings, [])
        self.assertEqual(next(c for c in result.checks if c.id == "fixture.files").status, "blocked")

    def test_plugin_load_failure_reduces_coverage_without_losing_builtin_findings(self):
        result = self.snapshot(warnings=["Plugin fixture could not load"])
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(next(c for c in result.checks if c.id == "plugins.load").status, "partial")
        self.assertIn("Plugin fixture could not load", result.warnings)

    def test_malformed_serialized_shapes_fail_as_validation_errors(self):
        valid = asdict(self.snapshot())
        mutations = [
            ("schema_version", True), ("schema_version", 1.0),
            ("findings", {}), ("findings", [None]), ("checks", {}),
            ("checks", [{"id": "fixture.files", "status": [], "detail": "bad"}]),
            ("warnings", "warning"), ("scope", []), ("id", []),
        ]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                data = deepcopy(valid)
                data[field] = value
                with self.assertRaises(ValueError):
                    snapshot_from_dict(data)
        for resource in ({"uri": "home://valid", "path": "/fixture", "mode": -1}, None):
            with self.subTest(resource=resource):
                data = deepcopy(valid)
                data["findings"][0]["resources"] = [resource]
                with self.assertRaises(ValueError):
                    snapshot_from_dict(data)

    def test_legacy_snapshot_without_scope_still_loads(self):
        data = asdict(self.snapshot())
        del data["scope"]
        self.assertEqual(snapshot_from_dict(data).scope, {})


class RegistryEdgeTests(unittest.TestCase):
    def test_malformed_metadata_is_rejected_before_plugin_runs(self):
        changes = [
            {"id": "plugins"}, {"id": "platform"}, {"id": []},
            {"title": None}, {"title": ""}, {"api_version": True},
            {"platforms": "linux"}, {"platforms": ()}, {"platforms": ([],)},
            {"platforms": ("linux", "linux")},
            {"checks": "fixture.files"}, {"checks": (("fixture.files",),)},
            {"checks": (("fixture.files", None),)}, {"checks": (("fixture.", "Empty suffix"),)},
            {"checks": (("other.files", "Wrong namespace"),)},
        ]
        for changeset in changes:
            with self.subTest(changes=changeset):
                module = FixtureModule()
                module.spec = replace(module.spec, **changeset)
                with self.assertRaises(ValueError):
                    validate_module(module)
        with self.assertRaises(ValueError):
            validate_module(object())

    def test_list_based_metadata_remains_supported(self):
        module = FixtureModule()
        module.spec = replace(module.spec, platforms=["linux"], checks=[["fixture.files", "Inspect"]])
        self.assertIs(validate_module(module), module)

    def test_enumeration_failure_keeps_builtin_catalog_and_warning(self):
        with patch("driftstamp.registry.entry_points", side_effect=RuntimeError("metadata broken")):
            modules, warnings = load_modules(True)
            catalog = module_catalog(True)
        self.assertTrue(modules)
        self.assertEqual(len(warnings), 1)
        self.assertIn("metadata broken", warnings[0])
        self.assertEqual(catalog[-1]["id"], "plugin-errors")

    def test_one_invalid_plugin_does_not_prevent_later_valid_plugin_loading(self):
        invalid = FixtureModule()
        invalid.spec = replace(invalid.spec, platforms="linux")
        entries = [Entry("a-invalid", lambda: invalid), Entry("b-valid", FixtureModule)]
        with patch("driftstamp.registry.entry_points", return_value=entries):
            modules, warnings = load_modules(True)
        self.assertEqual(sum(m.spec.id == "fixture" for m in modules), 1)
        self.assertEqual(len(warnings), 1)
        self.assertIn("a-invalid", warnings[0])


if __name__ == "__main__":
    unittest.main()
