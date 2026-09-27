"""Migration boundary regressions; no host configuration or package tools used."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

from driftstamp.migration import LinuxTarget, TargetSpec, export_bundle, load_target, plan_bundle
from test_core import Entry, FixtureCase


class BundleValidationEdges(FixtureCase):
    def setUp(self):
        super().setUp()
        self.output = self.bundle()
        self.manifest_path = self.output / "manifest.json"
        self.manifest = json.loads(self.manifest_path.read_text())

    def reject(self, manifest):
        self.manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            plan_bundle(self.output, "ubuntu")

    def test_noncanonical_resource_uris_are_rejected(self):
        for uri in ("home://.", "home://foo//bar", "home://foo/./bar", "home://foo/",
                    "home:///absolute", "home://../outside", "home://foo/../bar",
                    "home://a\\b", "home://a:b", "home://bad\x00name", "registry://key", None, []):
            with self.subTest(uri=uri):
                manifest = deepcopy(self.manifest)
                manifest["files"][0]["uri"] = uri
                manifest["findings"][0]["resources"][0]["uri"] = uri
                self.reject(manifest)

    def test_invalid_file_metadata_is_rejected_before_planning(self):
        cases = {"mode": [True, -1, 0o10000, "0755", []],
                 "symlink": [1, [], {}, "", "a\x00b"],
                 "sha256": [None, 1, [], "x", "A" * 64],
                 "kind": [None, [], "registry"],
                 "blob": [None, [], "../outside"]}
        for field, values in cases.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    manifest = deepcopy(self.manifest)
                    manifest["files"][0][field] = value
                    self.reject(manifest)

    def test_invalid_schema_and_warning_lists_fail_cleanly(self):
        for field, values in {"schema_version": [True, 1.0, "1", 2],
                              "warnings": [None, "warning", {}, [1], [[]], ["bad\x00text"]]}.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    manifest = deepcopy(self.manifest)
                    manifest[field] = value
                    self.reject(manifest)

    def test_source_fields_must_be_nonempty_strings(self):
        for field in ("platform", "distro", "scan"):
            for value in (None, [], "", "bad\x00text"):
                with self.subTest(field=field, value=value):
                    manifest = deepcopy(self.manifest)
                    manifest["source"][field] = value
                    self.reject(manifest)

    def test_missing_record_fields_are_clean_validation_errors(self):
        for section in ("file", "finding-resource"):
            for field in ("uri", "mode", "sha256", "kind", "symlink"):
                with self.subTest(section=section, field=field):
                    manifest = deepcopy(self.manifest)
                    record = manifest["files"][0] if section == "file" else manifest["findings"][0]["resources"][0]
                    del record[field]
                    self.reject(manifest)

    def test_duplicate_and_unreferenced_files_are_rejected(self):
        manifest = deepcopy(self.manifest)
        manifest["files"].append(deepcopy(manifest["files"][0]))
        self.reject(manifest)
        manifest = deepcopy(self.manifest)
        manifest["findings"][0]["resources"] = []
        self.reject(manifest)

    def test_finding_file_metadata_cannot_disagree(self):
        for field, value in (("uri", "home://other"), ("sha256", "f" * 64),
                             ("mode", 0), ("symlink", "other")):
            with self.subTest(field=field):
                manifest = deepcopy(self.manifest)
                manifest["findings"][0]["resources"][0][field] = value
                self.reject(manifest)

    def test_bad_dependencies_and_finding_warnings_fail_cleanly(self):
        for field, values in {"dependencies": [None, {}, [None], [""], ["a\x00b"]],
                              "warnings": [None, {}, [1], ["a\x00b"]]}.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    manifest = deepcopy(self.manifest)
                    manifest["findings"][0][field] = value
                    self.reject(manifest)

    def test_blob_symlink_cannot_redirect_content_reads(self):
        blob = self.output / self.manifest["files"][0]["blob"]
        outside = self.base / "same-content"
        outside.write_bytes(blob.read_bytes())
        blob.unlink()
        self.symlink(blob, outside)
        with self.assertRaisesRegex(ValueError, "escapes"):
            plan_bundle(self.output, "ubuntu")

    def test_all_associated_findings_contribute_migration_warnings(self):
        manifest = deepcopy(self.manifest)
        second = deepcopy(manifest["findings"][0])
        second["id"] = "another-finding"
        second["warnings"] = ["Machine-specific setting"]
        manifest["findings"].append(second)
        self.manifest_path.write_text(json.dumps(manifest))
        result = plan_bundle(self.output, "ubuntu")
        self.assertEqual(result["resources"][0]["action"], "adapt")


class ExportEdges(FixtureCase):
    def test_identical_content_is_stored_once_for_distinct_resources(self):
        other = self.home / "other.sh"
        other.write_bytes(self.script.read_bytes())
        snapshot = self.snapshot()
        snapshot.findings[0].resources.append(self.ctx.resource(other))
        output = self.base / "deduplicated"
        export_bundle(snapshot, {snapshot.findings[0].id: {"decision": "keep"}}, output)
        self.assertEqual(len(list((output / "blobs").iterdir())), 1)
        self.assertEqual(len(plan_bundle(output, "arch")["resources"]), 2)

    def test_changed_symlink_target_is_rejected_without_output(self):
        link = self.home / "alias"
        self.symlink(link, "custom.sh")
        snapshot = self.snapshot()
        snapshot.findings[0].resources = [self.ctx.resource(link)]
        link.unlink()
        self.symlink(link, "other.sh")
        output = self.base / "changed-link"
        with self.assertRaisesRegex(ValueError, "link changed"):
            export_bundle(snapshot, {snapshot.findings[0].id: {"decision": "keep"}}, output)
        self.assertFalse(output.exists())

    def test_dangling_output_symlink_is_not_replaced(self):
        output = self.base / "output-link"
        self.symlink(output, "nonexistent")
        snapshot = self.snapshot()
        with self.assertRaisesRegex(ValueError, "already exists"):
            export_bundle(snapshot, {snapshot.findings[0].id: {"decision": "keep"}}, output)
        self.assertTrue(output.is_symlink())

    def test_interrupted_manifest_write_cleans_staging_and_allows_retry(self):
        snapshot = self.snapshot()
        reviews = {snapshot.findings[0].id: {"decision": "keep"}}
        output = self.base / "retry"
        with patch("driftstamp.migration.atomic_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                export_bundle(snapshot, reviews, output)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.base.glob(".driftstamp-export-*")), [])
        export_bundle(snapshot, reviews, output)
        self.assertTrue((output / "manifest.json").is_file())

    def test_symlink_digest_must_match_literal_target(self):
        link = self.home / "alias"
        self.symlink(link, "custom.sh")
        snapshot = self.snapshot()
        snapshot.findings[0].resources = [self.ctx.resource(link)]
        output = self.base / "links"
        manifest = export_bundle(snapshot, {snapshot.findings[0].id: {"decision": "keep"}}, output)
        manifest["files"][0]["symlink"] = "changed-target"
        (output / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "Invalid bundled link"):
            plan_bundle(output, "arch")


class TargetAdapterEdges(FixtureCase):
    def test_target_entry_points_are_never_loaded_without_opt_in(self):
        with patch("driftstamp.migration.entry_points") as points:
            self.assertEqual(load_target("ubuntu").spec.id, "ubuntu")
        points.assert_not_called()

    def test_invalid_target_metadata_is_rejected(self):
        spec = TargetSpec("custom", "Custom", ("linux",))
        for field, values in {"id": [None, [], "ubuntu", "bad.name"],
                              "title": [None, "", "bad\x00title"],
                              "api_version": [True, 1.0, 2],
                              "platforms": ["linux", (), (None,), ("",), ["bad\x00platform"]]}.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    adapter = LinuxTarget("custom")
                    adapter.spec = replace(spec, **{field: value})
                    with patch("driftstamp.migration.entry_points", return_value=[Entry("custom", lambda: adapter)]):
                        with self.assertRaisesRegex(ValueError, "Target plugin"):
                            load_target("custom", True)

    def test_invalid_target_package_results_are_rejected(self):
        bundle = self.bundle()
        for value in ([], {}, 1, True, "", "--option", "two packages", "bad\x00name"):
            with self.subTest(value=value), patch.object(LinuxTarget, "resolve", return_value=value):
                with self.assertRaisesRegex(ValueError, "package name"):
                    plan_bundle(bundle, "ubuntu")

    def test_invalid_target_action_results_are_rejected(self):
        bundle = self.bundle()
        for value in (None, "copy", [], ("copy",), ([], "reason"),
                      ("delete", "reason"), ("copy", None), ("copy", ""), ("copy", "bad\x00reason")):
            with self.subTest(value=value), patch.object(LinuxTarget, "resource_action", return_value=value):
                with self.assertRaisesRegex(ValueError, "resource action"):
                    plan_bundle(bundle, "ubuntu")

    def test_invalid_target_install_arguments_are_rejected(self):
        bundle = self.bundle()
        for value in (None, "sudo apt install", ("apt",), [None], [[]], [""], ["bad\x00arg"]):
            with self.subTest(value=value), patch.object(LinuxTarget, "install_argv", return_value=value):
                with self.assertRaisesRegex(ValueError, "argument list"):
                    plan_bundle(bundle, "ubuntu")

    def test_adapter_method_failures_are_clean_plan_errors(self):
        bundle = self.bundle()
        for method in ("resolve", "resource_action", "install_argv"):
            with self.subTest(method=method), patch.object(LinuxTarget, method, side_effect=RuntimeError("adapter failed")):
                with self.assertRaisesRegex(ValueError, method):
                    plan_bundle(bundle, "ubuntu")


class HostConflictEdges(FixtureCase):
    def setUp(self):
        super().setUp()
        self.output = self.bundle()
        self.destination_home = self.base / "destination"
        self.destination_home.mkdir()
        self.destination = self.destination_home / "custom.sh"

    def plan(self, output=None):
        with patch("driftstamp.migration.Path.home", return_value=self.destination_home), \
                patch("driftstamp.migration._host_distro", return_value="ubuntu"):
            return plan_bundle(output or self.output, "ubuntu", check_host=True)["resources"][0]

    def test_matching_destination_is_omitted_and_different_content_needs_merge(self):
        self.destination.write_bytes(self.script.read_bytes())
        self.destination.chmod(self.script.stat().st_mode)
        self.assertEqual(self.plan()["action"], "omit")
        self.destination.write_text("different content")
        self.assertEqual(self.plan()["action"], "merge")

    def test_destination_permission_difference_requires_review(self):
        if os.name == "nt":
            self.skipTest("POSIX permissions unavailable")
        self.destination.write_bytes(self.script.read_bytes())
        self.destination.chmod(self.script.stat().st_mode ^ 0o100)
        result = self.plan()
        self.assertEqual(result["action"], "review")
        self.assertIn("permissions differ", result["reason"])

    def test_destination_directory_and_dangling_symlink_require_review(self):
        self.destination.mkdir()
        self.assertEqual(self.plan()["action"], "review")
        self.destination.rmdir()
        self.symlink(self.destination, "missing")
        self.assertEqual(self.plan()["action"], "review")

    def test_symlink_parent_is_reviewed_without_reading_destination(self):
        actual = self.base / "actual-destination"
        actual.mkdir()
        (actual / "custom.sh").write_bytes(self.script.read_bytes())
        self.destination_home.rmdir()
        self.symlink(self.destination_home, actual)
        from driftstamp import migration
        original_read = migration._read_regular
        def guarded_read(path):
            self.assertNotEqual(path, self.destination)
            return original_read(path)
        with patch("driftstamp.migration._read_regular", side_effect=guarded_read):
            result = self.plan()
        self.assertEqual(result["action"], "review")
        self.assertIn("parent is a symbolic link", result["reason"])

    def test_parent_file_is_a_conflict_not_an_absent_destination(self):
        self.destination_home.rmdir()
        self.destination_home.write_text("not a directory")
        result = self.plan()
        self.assertEqual(result["action"], "review")
        self.assertIn("not a directory", result["reason"])

    def test_source_link_cannot_match_regular_destination_content(self):
        link = self.home / "alias"
        self.symlink(link, "custom.sh")
        snapshot = self.snapshot()
        snapshot.findings[0].resources = [self.ctx.resource(link)]
        output = self.base / "source-link"
        export_bundle(snapshot, {snapshot.findings[0].id: {"decision": "keep"}}, output)
        (self.destination_home / "alias").write_text("custom.sh")
        result = self.plan(output)
        self.assertEqual(result["action"], "review")
        self.assertIn("source is a symbolic link", result["reason"])

    def test_unreadable_destination_is_reviewed_without_aborting_plan(self):
        self.destination.write_text("existing content")
        from driftstamp import migration
        original_read = migration._read_regular
        def denied_read(path):
            if path == self.destination:
                raise PermissionError("fixture denial")
            return original_read(path)
        with patch("driftstamp.migration._read_regular", side_effect=denied_read):
            result = self.plan()
        self.assertEqual(result["action"], "review")
        self.assertIn("could not be checked", result["reason"])
