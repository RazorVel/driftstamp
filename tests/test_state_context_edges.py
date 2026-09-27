"""Corrupt state and scan-boundary regressions; no live host inspection."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import unittest
from unittest.mock import patch

from driftstamp import cli
from driftstamp.context import ScanContext
from driftstamp.model import Resource, validate_resource
from driftstamp.store import atomic_json
from test_core import FixtureCase


class ContextEdgeTests(FixtureCase):
    def test_missing_home_cannot_be_an_empty_successful_user_scan(self):
        for scope in ("user", "all"):
            with self.subTest(scope=scope), self.assertRaisesRegex(ValueError, "existing directory"):
                ScanContext(self.root, self.home / "typo", scope=scope)
        ScanContext(self.root, self.home / "absent", scope="system")

    def test_home_must_resolve_inside_root(self):
        outside = self.base / "outside"
        outside.mkdir()
        alias = self.root / "home-link"
        self.symlink(alias, outside)
        with self.assertRaisesRegex(ValueError, "inside"):
            ScanContext(self.root, alias)

    def test_symlink_loop_is_rejected_as_invalid_input(self):
        loop = self.home / "loop"
        self.symlink(loop, "loop")
        self.assertFalse(self.ctx.allowed(loop))
        with self.assertRaises(ValueError):
            ScanContext(self.root, loop)

    def test_directory_exclusion_also_blocks_direct_child_references(self):
        private = self.home / "private"
        private.mkdir()
        secret = private / "secret.sh"
        secret.write_text("#!/bin/sh\necho secret\n")
        for exclude in ("home://private", "~/private", str(private), "home/alice/private"):
            with self.subTest(exclude=exclude):
                self.ctx.excludes = (exclude,)
                self.assertIsNone(self.ctx.resolve_reference("~/private/secret.sh", self.home))
                with self.assertRaises(ValueError):
                    self.ctx.read_text(secret)

    def test_file_alias_cannot_bypass_exclusion_of_target(self):
        alias = self.home / "alias"
        self.symlink(alias, self.script.name)
        self.ctx.excludes = ("home://custom.sh",)
        self.assertFalse(self.ctx.allowed(alias))
        with self.assertRaises(ValueError):
            self.ctx.resource(alias)

    def test_exclusions_work_with_symlinked_scan_root(self):
        alias = self.base / "root-alias"
        self.symlink(alias, self.root)
        ctx = ScanContext(alias, alias / "home/alice", excludes=("home://custom.sh",))
        self.assertFalse(ctx.allowed(alias / "home/alice/custom.sh"))

    def test_exclusions_work_through_symlinked_home(self):
        alias = self.root / "alice-alias"
        self.symlink(alias, self.home)
        ctx = ScanContext(self.root, alias, excludes=("home://custom.sh",))
        self.assertFalse(ctx.allowed(self.script))

    def test_directory_links_are_not_recursively_followed(self):
        linked = self.home / "linked"
        self.symlink(linked, self.home)
        paths = self.ctx.walk_files(self.home)
        self.assertEqual(paths, sorted([self.script, linked]))
        self.assertTrue(any(issue["status"] == "partial" for issue in self.ctx.issues))

    def test_entry_limit_is_explicit_instead_of_silent_truncation(self):
        (self.home / "second.sh").write_text("#!/bin/sh\n")
        with patch("driftstamp.context.MAX_ENTRIES", 1):
            with self.assertRaisesRegex(ValueError, "entry inspection limit"):
                self.ctx.walk_files(self.home)

    def test_byte_limit_boundary_and_invalid_utf8(self):
        self.script.write_bytes(b"1234")
        with patch("driftstamp.context.MAX_FILE_BYTES", 4):
            self.assertEqual(self.ctx.read_text(self.script), "1234")
            self.script.write_bytes(b"12345")
            with self.assertRaisesRegex(ValueError, "byte inspection limit"):
                self.ctx.read_text(self.script)
        self.script.write_bytes(b"\xff")
        with self.assertRaises(UnicodeError):
            self.ctx.read_text(self.script)


class StateEdgeTests(FixtureCase):
    def test_malformed_latest_pointers_are_data_errors(self):
        self.store.save(self.snapshot())
        pointer = self.store.directory / "latest.json"
        for value in (None, [], {}, {"id": []}, {"id": "../escape"}, {"id": "latest"}):
            with self.subTest(value=value):
                pointer.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    self.store.load()

    def test_snapshot_name_and_embedded_id_must_agree(self):
        self.store.save(self.snapshot())
        saved = self.store.directory / "snapshots/first.json"
        data = json.loads(saved.read_text())
        data["id"] = "different"
        saved.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "saved name"):
            self.store.load("first")

    def test_invalid_records_cannot_be_saved(self):
        snapshot = self.snapshot()
        snapshot.findings[0].resources[0].sha256 = "bad"
        with self.assertRaises(ValueError):
            self.store.save(snapshot)
        self.assertFalse(self.store.directory.exists())

    def test_corrupt_snapshot_json_is_friendly(self):
        self.store.save(self.snapshot())
        (self.store.directory / "snapshots/first.json").write_text("{")
        with self.assertRaisesRegex(ValueError, "Invalid snapshot state"):
            self.store.load()

    def test_invalid_review_decisions_and_notes_are_rejected(self):
        snapshot = self.snapshot()
        self.store.save(snapshot)
        fid = snapshot.findings[0].id
        for decision, note in (([], None), ({}, None), ("keep", [])):
            with self.subTest(decision=decision, note=note), self.assertRaises(ValueError):
                self.store.review(fid, decision, note)
        review = self.store.directory / "reviews.json"
        for record in ([], {fid: []}, {fid: {"decision": [], "note": ""}},
                       {fid: {"decision": "keep", "note": None}}):
            with self.subTest(record=record):
                review.write_text(json.dumps(record))
                with self.assertRaises(ValueError):
                    self.store.reviews()

    def test_note_omission_preserves_and_empty_text_clears(self):
        snapshot = self.snapshot()
        self.store.save(snapshot)
        fid = snapshot.findings[0].id
        self.store.review(fid, "keep", "Remember why")
        self.assertEqual(self.store.review(fid, "investigate")["note"], "Remember why")
        self.assertEqual(self.store.review(fid, "keep", "")["note"], "")

    def test_failed_review_publication_preserves_previous_state(self):
        snapshot = self.snapshot()
        self.store.save(snapshot)
        fid = snapshot.findings[0].id
        old = self.store.review(fid, "keep", "Original")
        with patch("driftstamp.store.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.review(fid, "skip", "New")
        self.assertEqual(self.store.reviews()[fid], old)
        self.assertFalse(list(self.store.directory.glob(".driftstamp-*")))

    def test_failure_publishing_latest_leaves_recoverable_named_snapshot(self):
        self.store.save(self.snapshot())
        with patch("driftstamp.store.atomic_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.save(self.snapshot("second"))
        self.assertEqual(self.store.load().id, "first")
        self.assertEqual(self.store.load("second").id, "second")
        self.assertFalse(list((self.store.directory / "snapshots").glob(".snapshot-*")))


class CliInputEdgeTests(FixtureCase):
    def call(self, *args):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = cli.main([*args, "--state-dir", str(self.store.directory)])
        return code, output.getvalue(), errors.getvalue()

    def test_bad_scan_names_rejected_before_scanning(self):
        for name in ("latest", "../oops", "", "x" * 81):
            with self.subTest(name=name), patch.object(cli, "scan_system") as scanner:
                code, out, err = self.call("scan", "--name", name)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertNotIn("Traceback", err)
                scanner.assert_not_called()

    def test_missing_home_is_usage_error_without_snapshot(self):
        code, out, err = self.call("scan", "--root", str(self.root), "--home", str(self.home / "typo"))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("existing directory", err)
        self.assertFalse(self.store.directory.exists())

    def test_looping_home_is_usage_error(self):
        link = self.home / "loop"
        self.symlink(link, "loop")
        code, out, err = self.call("scan", "--root", str(self.root), "--home", str(link))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertNotIn("Traceback", err)

    def test_os_release_loop_reports_unknown_distro(self):
        etc = self.root / "etc"
        etc.mkdir()
        self.symlink(etc / "os-release", "os-release")
        self.assertEqual(cli._detect_distro(self.root, "linux"), "unknown")

    def test_corrupt_latest_is_stderr_only_for_json_commands(self):
        atomic_json(self.store.directory / "latest.json", {"id": []})
        code, out, err = self.call("list", "--format", "json")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertNotIn("Traceback", err)


class ResourceValidationTests(unittest.TestCase):
    def test_future_resource_types_remain_extensible(self):
        validate_resource(Resource("registry://HKCU/Software/Example", "", kind="registry"))

    def test_malformed_file_metadata_is_rejected(self):
        for field, value in (("uri", "home://../escape"), ("uri", "home://."),
                             ("uri", "home://a//b"), ("sha256", "wrong"),
                             ("mode", True), ("mode", -1), ("mode", 0o10000),
                             ("symlink", ""), ("symlink", []), ("path", "a\x00b")):
            with self.subTest(field=field, value=value):
                resource = Resource("home://script", "/home/alice/script")
                setattr(resource, field, value)
                with self.assertRaises(ValueError):
                    validate_resource(resource)


if __name__ == "__main__":
    unittest.main()
