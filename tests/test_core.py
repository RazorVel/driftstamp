"""Deterministic acceptance tests: temporary roots, fake clocks, no host commands."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from driftstamp.context import MAX_FILE_BYTES, ScanContext
from driftstamp.engine import compare, scan, snapshot_from_dict
from driftstamp.migration import TargetSpec, export_bundle, load_target, plan_bundle
from driftstamp.model import Check, Finding, ModuleResult, ModuleSpec, Snapshot, coverage, finding_id
from driftstamp.registry import load_modules
from driftstamp.store import Store


class SampleModule:
    spec = ModuleSpec("sample", "Sample fixture module", ("linux",), (("sample.files", "Inspect sample files"),))

    def scan(self, ctx):
        path = ctx.home / "custom.sh"
        findings = []
        if path.exists():
            findings.append(Finding(finding_id("sample", "home://custom.sh"), "sample", "Custom script", "User script candidate", [ctx.resource(path)], ["Known local path"], 75, ["xrandr", "unknown-tool"]))
        return ModuleResult(findings, [Check("sample.files", "complete", "Fixture inspected")])


class FailingModule:
    spec = SampleModule.spec

    def scan(self, ctx):
        raise PermissionError("fixture permission denied")


class Entry:
    def __init__(self, name, factory):
        self.name, self.factory = name, factory

    def load(self):
        return self.factory


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "root"
        self.home = self.root / "home/alice"
        self.home.mkdir(parents=True)
        self.script = self.home / "custom.sh"
        self.script.write_text("#!/bin/sh\nxrandr --query\n", encoding="utf-8")
        self.ctx = ScanContext(self.root, self.home, distro="ubuntu")
        self.store = Store(self.base / "state")

    def snapshot(self, name="first"):
        return scan(self.ctx, modules=[SampleModule()], name=name, now="2026-01-01T00:00:00+00:00")

    def bundle(self):
        snapshot = self.snapshot()
        output = self.base / "bundle"
        export_bundle(snapshot, {snapshot.findings[0].id: {"decision": "keep", "note": "Desk display"}}, output)
        return output

    def symlink(self, destination, target):
        try:
            destination.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation is unavailable on this test host")


class CoverageTests(unittest.TestCase):
    def test_incomplete_and_excluded_cases_do_not_inflate_score(self):
        checks = [Check(str(i), status, status) for i, status in enumerate(("complete", "partial", "excluded", "unsupported", "not_applicable"))]
        self.assertEqual(coverage(checks)["percent"], 25.0)
        self.assertEqual(coverage(checks)["total"], 4)

    def test_no_applicable_cases_is_unknown_not_full_coverage(self):
        self.assertIsNone(coverage([Check("x", "not_applicable", "x")])["percent"])

    def test_invalid_scores_and_duplicate_checks_are_rejected(self):
        with self.assertRaises(ValueError):
            Finding("x", "sample", "x", "x", [], [], 101)
        with self.assertRaises(ValueError):
            coverage([Check("x", "complete", "x"), Check("x", "complete", "x")])
        with self.assertRaises(ValueError):
            Check("x", "complete", "x", 0)


class ContextTests(FixtureCase):
    def test_stable_uris_do_not_include_temporary_root(self):
        self.assertEqual(self.ctx.resource(self.script).uri, "home://custom.sh")
        self.assertEqual(self.ctx.uri(self.root / "etc/fstab"), "system://etc/fstab")

    def test_literal_references_map_to_fixture_never_execute_expressions(self):
        for reference in ("~/custom.sh", "$HOME/custom.sh", "${HOME}/custom.sh", "/home/alice/custom.sh"):
            self.assertEqual(self.ctx.resolve_reference(reference, self.home), self.script)
        self.assertIsNone(self.ctx.resolve_reference("$(touch /tmp/should-not-exist)", self.home))
        self.assertIsNone(self.ctx.resolve_reference("../../../../outside", self.home))

    def test_exclusion_applies_to_reads_and_nested_traversal(self):
        self.ctx.excludes = ("home://custom.sh",)
        with self.assertRaises(ValueError):
            self.ctx.read_text(self.script)
        self.assertEqual(self.ctx.walk_files(self.home), [])
        self.assertEqual(self.ctx.issues[0]["status"], "excluded")

    def test_binary_and_oversize_inputs_are_explicit_failures(self):
        self.script.write_bytes(b"\x00ELF")
        with self.assertRaises(ValueError):
            self.ctx.read_text(self.script)
        self.script.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
        with self.assertRaises(ValueError):
            self.ctx.resource(self.script)

    def test_symlink_cannot_escape_fixture_root(self):
        outside = self.base / "outside"
        outside.write_text("private")
        link = self.home / "link"
        self.symlink(link, outside)
        self.assertFalse(self.ctx.allowed(link))
        with self.assertRaises(ValueError):
            self.ctx.read_text(link)

    def test_symlink_identity_records_literal_target(self):
        link = self.home / "alias"
        self.symlink(link, "custom.sh")
        resource = self.ctx.resource(link)
        self.assertEqual(resource.symlink, "custom.sh")
        self.assertEqual(resource.uri, "home://alias")


class EngineTests(FixtureCase):
    def test_fixed_clock_snapshot_roundtrip(self):
        snapshot = self.snapshot()
        self.assertEqual(asdict(snapshot_from_dict(asdict(snapshot))), asdict(snapshot))
        self.assertEqual(snapshot.created_at, "2026-01-01T00:00:00+00:00")

    def test_missing_linux_use_cases_are_explicit_not_silently_complete(self):
        snapshot = self.snapshot()
        self.assertIn("package-config.baselines", {c.id for c in snapshot.checks if c.status == "unsupported"})
        self.assertLess(coverage(snapshot.checks)["percent"], 100)

    def test_windows_reports_pending_capabilities_and_no_false_success(self):
        self.ctx.platform = "windows"
        snapshot = self.snapshot()
        self.assertFalse(snapshot.findings)
        self.assertEqual(coverage(snapshot.checks)["percent"], 0)
        self.assertIn("powershell.profiles", {c.id for c in snapshot.checks})

    def test_module_failure_preserves_report_and_blocks_checks(self):
        snapshot = scan(self.ctx, modules=[FailingModule()], name="failed")
        self.assertIn("blocked", {c.status for c in snapshot.checks})
        self.assertTrue(snapshot.warnings)

    def test_incomplete_module_cannot_claim_a_deleted_customization(self):
        before = self.snapshot()
        after = scan(self.ctx, modules=[FailingModule()], name="failed")
        result = compare(before, after)
        self.assertFalse(result["removed"])
        self.assertEqual(result["not_observed"], [before.findings[0].id])

    def test_complete_rescan_detects_actual_removal(self):
        before = self.snapshot()
        self.script.unlink()
        result = compare(before, self.snapshot("after"))
        self.assertEqual(result["removed"], [before.findings[0].id])

    def test_edit_preserves_id_and_reports_change(self):
        before = self.snapshot()
        self.script.write_text("#!/bin/sh\nxrandr --auto\n")
        after = self.snapshot("after")
        self.assertEqual(before.findings[0].id, after.findings[0].id)
        self.assertEqual(compare(before, after)["changed"], [before.findings[0].id])

    def test_missing_check_result_is_partial(self):
        module = SampleModule()
        module.scan = lambda ctx: ModuleResult()
        result = scan(self.ctx, modules=[module], name="missing")
        self.assertEqual(next(c for c in result.checks if c.id == "sample.files").status, "partial")

    def test_invalid_plugin_data_is_isolated(self):
        module = SampleModule()
        module.scan = lambda ctx: ModuleResult(checks=[Check("other.check", "complete", "invalid")])
        result = scan(self.ctx, modules=[module], name="invalid")
        self.assertEqual(next(c for c in result.checks if c.id == "sample.files").status, "blocked")

    def test_schema_and_path_names_are_validated(self):
        for payload in ([], {"schema_version": 2}, {"schema_version": 1}):
            with self.assertRaises(ValueError):
                snapshot_from_dict(payload)
        for name in ("../escape", "latest", "", "/tmp/escape"):
            with self.assertRaises(ValueError):
                self.snapshot(name)


class PluginTests(unittest.TestCase):
    def test_external_code_never_loads_without_opt_in(self):
        with patch("driftstamp.registry.entry_points") as points:
            load_modules()
        points.assert_not_called()

    def test_compatible_external_plugin_needs_no_core_edit(self):
        with patch("driftstamp.registry.entry_points", return_value=[Entry("sample", SampleModule)]):
            modules, warnings = load_modules(True)
        self.assertIn("sample", {m.spec.id for m in modules})
        self.assertEqual(warnings, [])

    def test_duplicate_and_incompatible_plugins_cannot_shadow_builtins(self):
        class WrongVersion(SampleModule):
            spec = ModuleSpec("future", "Future", ("linux",), (("future.case", "future"),), 2)
        with patch("driftstamp.registry.entry_points", return_value=[Entry("first", SampleModule), Entry("duplicate", SampleModule), Entry("future", WrongVersion)]):
            modules, warnings = load_modules(True)
        self.assertEqual(sum(m.spec.id == "sample" for m in modules), 1)
        self.assertEqual(len(warnings), 2)

    def test_new_target_can_resolve_dependencies_without_core_edit(self):
        class Target:
            spec = TargetSpec("redhat-example", "Example only", ("linux",))

            def resolve(self, command, release=None):
                return "example-package" if command == "example-command" else None

            def install_argv(self, packages):
                return ["example-manager", "install", *packages]

            def resource_action(self, resource, finding):
                return "review", "Example only"
        with patch("driftstamp.migration.entry_points", return_value=[Entry("example", Target)]):
            self.assertEqual(load_target("redhat-example", True).resolve("example-command"), "example-package")
        with self.assertRaises(ValueError):
            load_target("redhat-example")


class StoreTests(FixtureCase):
    def test_named_snapshots_are_immutable(self):
        snapshot = self.snapshot()
        self.store.save(snapshot)
        with self.assertRaises(ValueError):
            self.store.save(snapshot)
        self.assertEqual(self.store.load().id, snapshot.id)

    def test_reviews_survive_later_scans(self):
        snapshot = self.snapshot()
        self.store.save(snapshot)
        key = snapshot.findings[0].id
        self.store.review(key, "keep", "Required for desk monitor")
        self.store.save(self.snapshot("second"))
        self.store.review(key, "investigate")
        self.assertEqual(self.store.reviews()[key]["note"], "Required for desk monitor")

    def test_unknown_findings_and_decisions_are_rejected(self):
        self.store.save(self.snapshot())
        with self.assertRaises(ValueError):
            self.store.review("missing", "keep")
        with self.assertRaises(ValueError):
            self.store.review("missing", "nonsense")

    def test_invalid_review_records_fail_cleanly(self):
        self.store.directory.mkdir()
        (self.store.directory / "reviews.json").write_text('{"id": "keep"}')
        with self.assertRaises(ValueError):
            self.store.reviews()


class MigrationTests(FixtureCase):
    def test_only_reviewed_content_is_exported_and_original_is_unchanged(self):
        before = self.script.read_bytes()
        output = self.bundle()
        manifest = json.loads((output / "manifest.json").read_text())
        self.assertEqual((output / manifest["files"][0]["blob"]).read_bytes(), before)
        self.assertEqual(self.script.read_bytes(), before)
        self.assertEqual(manifest["findings"][0]["review"]["note"], "Desk display")

    def test_no_selection_creates_no_bundle(self):
        output = self.base / "empty"
        with self.assertRaises(ValueError):
            export_bundle(self.snapshot(), {}, output)
        self.assertFalse(output.exists())

    def test_changed_source_is_rejected_before_output_creation(self):
        snapshot = self.snapshot()
        self.script.write_text("changed")
        with self.assertRaisesRegex(ValueError, "changed since scan"):
            export_bundle(snapshot, {snapshot.findings[0].id: {"decision": "keep"}}, self.base / "changed")
        self.assertFalse((self.base / "changed").exists())

    @unittest.skipIf(os.name == "nt", "POSIX permissions unavailable")
    def test_permission_change_is_not_silently_exported(self):
        snapshot = self.snapshot()
        self.script.chmod(self.script.stat().st_mode ^ 0o100)
        with self.assertRaisesRegex(ValueError, "changed since scan"):
            export_bundle(snapshot, {snapshot.findings[0].id: {"decision": "keep"}}, self.base / "changed-mode")

    def test_existing_output_is_never_overwritten(self):
        output = self.bundle()
        with self.assertRaisesRegex(ValueError, "already exists"):
            export_bundle(self.snapshot(), {}, output)

    def test_arch_and_ubuntu_mapping_and_unknown_dependency(self):
        bundle = self.bundle()
        arch = plan_bundle(bundle, "arch")
        ubuntu = plan_bundle(bundle, "ubuntu", "24.04")
        self.assertEqual(arch["packages"], ["xorg-xrandr"])
        self.assertEqual(ubuntu["packages"], ["x11-xserver-utils"])
        self.assertEqual(arch["unresolved_dependencies"], ["unknown-tool"])
        self.assertFalse(arch["host_checked"])

    def test_content_tampering_is_rejected(self):
        bundle = self.bundle()
        blob = next((bundle / "blobs").iterdir())
        blob.write_text("tampered")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            plan_bundle(bundle, "arch")

    def test_malicious_bundle_path_is_rejected(self):
        bundle = self.bundle()
        path = bundle / "manifest.json"
        data = json.loads(path.read_text())
        data["files"][0]["blob"] = "../../outside"
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "content path"):
            plan_bundle(bundle, "arch")

    def test_malformed_manifest_is_a_user_error(self):
        bundle = self.base / "invalid"
        bundle.mkdir()
        (bundle / "manifest.json").write_text("[]")
        with self.assertRaises(ValueError):
            plan_bundle(bundle, "arch")

    def test_host_check_rejects_different_destination(self):
        bundle = self.bundle()
        with patch("driftstamp.migration._host_distro", return_value="ubuntu"):
            with self.assertRaisesRegex(ValueError, "does not match"):
                plan_bundle(bundle, "arch", check_host=True)

    def test_cross_os_migration_is_not_implied_by_portable_core(self):
        bundle = self.bundle()
        path = bundle / "manifest.json"
        data = json.loads(path.read_text())
        data["source"]["platform"] = "windows"
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "cross-OS"):
            plan_bundle(bundle, "arch")

    def test_symlinks_are_metadata_never_materialized_on_export(self):
        link = self.home / "alias"
        self.symlink(link, "custom.sh")
        snapshot = self.snapshot()
        snapshot.findings[0].resources.append(self.ctx.resource(link))
        output = self.base / "links"
        manifest = export_bundle(snapshot, {snapshot.findings[0].id: {"decision": "keep"}}, output)
        record = next(r for r in manifest["files"] if r["symlink"])
        self.assertIsNone(record["blob"])
        self.assertFalse(any(p.is_symlink() for p in output.rglob("*")))
        result = plan_bundle(output, "arch")
        self.assertEqual(next(r for r in result["resources"] if r["uri"] == "home://alias")["action"], "review")


if __name__ == "__main__":
    unittest.main()
