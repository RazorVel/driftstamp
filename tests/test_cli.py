"""Deterministic CLI tests: temporary files, fixed scans, no host discovery."""

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from driftstamp import cli
from driftstamp.model import Check, Finding, ModuleSpec, Snapshot
from driftstamp.store import Store


class FakeModule:
    spec = ModuleSpec("scripts", "Local scripts", ("linux",), (("scripts.files", "Find scripts"),))


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="driftstamp-cli-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "fixture"
        self.home = self.root / "home" / "tester"
        self.home.mkdir(parents=True)
        self.state = self.base / "state"
        self.script = self.home / "hello.sh"
        self.script.write_text("#!/bin/sh\nprintf '%s\\n' hello\n", encoding="utf-8")
        (self.root / "etc").mkdir()
        (self.root / "etc" / "os-release").write_text('ID="ubuntu"\n', encoding="utf-8")
        self.environ = patch.dict(os.environ, {"HOME": str(self.home),
                                               "XDG_STATE_HOME": str(self.base / "xdg")}, clear=True)
        self.environ.start()
        self.addCleanup(self.environ.stop)
        self.contexts = []

    def call(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(list(arguments))
        return code, stdout.getvalue(), stderr.getvalue()

    def saved_scan(self, name="fixture", status="complete", warnings=()):
        ctx = cli.ScanContext(self.root, self.home)
        return Snapshot(
            id=name, created_at="2026-01-02T03:04:05+00:00", platform="linux", distro="ubuntu",
            home=str(self.home),
            findings=[Finding("scripts-hello", "scripts", "Greeting script", "A local script.",
                              [ctx.resource(self.script)], ["Found in a fixture"], 85,
                              dependencies=["printf"], warnings=["Review absolute paths"]),
                      Finding("scripts-unused", "scripts", "Unused script", "Needs investigation.",
                              [], ["Unreferenced fixture"], 40)],
            checks=[Check("scripts.files", status, "Deterministic fixture check")],
            warnings=list(warnings),
        )

    def persist(self, name="fixture", status="complete"):
        snapshot = self.saved_scan(name, status)
        Store(self.state).save(snapshot)
        return snapshot

    def scan(self, *extra, status="complete"):
        def fake_scan(ctx, modules=None, selected=None, name=None, warnings=None):
            self.contexts.append(ctx)
            return self.saved_scan(name or "fixture", status)

        with patch.object(cli, "scan_system", side_effect=fake_scan), \
                patch.object(cli, "load_modules", return_value=([FakeModule()], [])):
            return self.call("--state-dir", str(self.state), "scan", "--root", str(self.root),
                             "--home", str(self.home), "--platform", "linux", *extra)

    def test_global_flags_work_on_either_side_and_later_value_wins(self):
        for argv in (("--format", "json", "modules"), ("modules", "--format", "json"),
                     ("--format", "text", "modules", "--format", "json")):
            with self.subTest(argv=argv), patch.object(cli, "module_catalog", return_value=[]):
                code, out, err = self.call(*argv)
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(out)["modules"], [])
                self.assertEqual(err, "")

    def test_json_scan_round_trip_and_original_context(self):
        code, out, err = self.scan("--format", "json", "--name", "before", "--scope", "user",
                                   "--include", str(self.home), "--exclude", "*.cache")
        self.assertEqual((code, err), (0, ""))
        result = json.loads(out)
        self.assertEqual(result["snapshot"]["id"], "before")
        self.assertEqual(result["coverage"]["percent"], 100)
        self.assertEqual(asdict(Store(self.state).load("before")), result["snapshot"])
        self.assertEqual(self.contexts[0].scope, "user")
        self.assertEqual(self.contexts[0].distro, "ubuntu")
        self.assertEqual(self.contexts[0].includes, (self.home,))
        self.assertEqual(self.contexts[0].excludes, ("*.cache",))

    def test_fixture_scan_review_export_plan_end_to_end(self):
        self.script.write_text("#!/usr/bin/python3\nprint('hello')\n", encoding="utf-8")
        real_scan = cli.scan_system

        def fixed_scan(ctx, **kwargs):
            return real_scan(ctx, now="2026-01-02T03:04:05+00:00", **kwargs)

        with patch.object(cli, "scan_system", side_effect=fixed_scan):
            code, out, err = self.call("scan", "--root", str(self.root), "--home", str(self.home),
                                      "--platform", "linux", "--module", "scripts", "--scope", "user",
                                      "--include", str(self.script), "--name", "workflow",
                                      "--state-dir", str(self.state), "--format", "json")
        self.assertEqual((code, err), (0, ""))
        snapshot = json.loads(out)["snapshot"]
        self.assertEqual(snapshot["created_at"], "2026-01-02T03:04:05+00:00")
        self.assertEqual(len(snapshot["findings"]), 1)
        finding_id = snapshot["findings"][0]["id"]
        code, _, err = self.call("review", finding_id, "--decision", "keep",
                                 "--state-dir", str(self.state), "--format", "json")
        self.assertEqual((code, err), (0, ""))
        bundle = self.base / "workflow-bundle"
        code, out, err = self.call("export", "--output", str(bundle), "--state-dir", str(self.state),
                                   "--format", "json")
        self.assertEqual((code, err), (0, ""))
        manifest = json.loads(out)
        self.assertEqual(len(manifest["files"]), 1)
        self.assertEqual((bundle / manifest["files"][0]["blob"]).read_bytes(), self.script.read_bytes())
        code, out, err = self.call("plan", str(bundle), "--target", "arch", "--format", "json")
        self.assertEqual((code, err), (0, ""))
        plan = json.loads(out)
        self.assertEqual(plan["packages"], ["python"])
        self.assertFalse(plan["host_checked"])
        self.assertEqual(plan["source"]["scan"], "workflow")

    def test_partial_and_blocked_scans_are_saved_but_exit_three(self):
        for status in ("partial", "blocked"):
            with self.subTest(status=status):
                code, out, err = self.scan("--format", "json", "--name", status, status=status)
                self.assertEqual(code, 3)
                self.assertEqual(json.loads(out)["coverage"]["percent"], 0)
                self.assertEqual(Store(self.state).load(status).checks[0].status, status)
                self.assertEqual(err, "")

    def test_explicit_scope_gaps_do_not_use_partial_error_exit(self):
        for status in ("excluded", "unsupported"):
            with self.subTest(status=status):
                code, out, _ = self.scan("--format", "json", "--name", status, status=status)
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(out)["coverage"]["percent"], 0)

    def test_fixture_requires_explicit_contained_home(self):
        cases = (("--root", str(self.root)),
                 ("--root", str(self.root), "--home", str(self.base / "elsewhere")))
        for args in cases:
            with self.subTest(args=args), patch.object(cli, "scan_system") as scanner:
                code, out, err = self.call("scan", "--state-dir", str(self.state), *args)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn("--home", err)
                scanner.assert_not_called()

    def test_unknown_module_is_argument_error(self):
        code, _, err = self.scan("--module", "does-not-exist")
        self.assertEqual(code, 2)
        self.assertIn("unknown module", err)
        self.assertEqual(self.contexts, [])

    def test_list_filters_and_review_persistence(self):
        self.persist()
        code, _, err = self.call("review", "scripts-hello", "--decision", "keep", "--note", "Desk setup",
                                 "--state-dir", str(self.state))
        self.assertEqual((code, err), (0, ""))
        code, out, _ = self.call("list", "--state-dir", str(self.state), "--format", "json",
                                 "--module", "scripts", "--decision", "keep", "--min-confidence", "80")
        findings = json.loads(out)["findings"]
        self.assertEqual(code, 0)
        self.assertEqual([item["id"] for item in findings], ["scripts-hello"])
        self.assertEqual(findings[0]["review"]["note"], "Desk setup")
        code, out, _ = self.call("list", "--state-dir", str(self.state), "--format", "json", "--needs-review")
        self.assertEqual(code, 0)
        self.assertEqual([item["id"] for item in json.loads(out)["findings"]], ["scripts-unused"])

    def test_show_reports_evidence_and_missing_finding_is_friendly(self):
        self.persist()
        code, out, err = self.call("show", "scripts-hello", "--state-dir", str(self.state))
        self.assertEqual((code, err), (0, ""))
        for value in ("85/100", "not a probability", "Found in a fixture", "printf", "Review absolute paths"):
            self.assertIn(value, out)
        code, out, err = self.call("show", "unknown", "--state-dir", str(self.state))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("does not exist", err)
        self.assertNotIn("Traceback", err)

    def test_coverage_has_check_details_and_independent_score(self):
        self.persist(status="blocked")
        code, out, _ = self.call("coverage", "--state-dir", str(self.state), "--format", "json")
        result = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(result["coverage"]["percent"], 0)
        self.assertEqual(result["checks"][0]["status"], "blocked")
        self.assertIn("interpretation", result["coverage"])

    def test_export_uses_reviewed_findings(self):
        snapshot = self.persist()
        Store(self.state).review("scripts-hello", "keep", note="Keep this")
        destination = self.base / "bundle"
        with patch.object(cli, "export_bundle", return_value={"output": str(destination)}) as exporter:
            code, out, err = self.call("export", "--output", str(destination), "--state-dir", str(self.state),
                                      "--format", "json")
        self.assertEqual((code, err), (0, ""))
        args = exporter.call_args.args
        self.assertEqual(asdict(args[0]), asdict(snapshot))
        self.assertEqual(args[1]["scripts-hello"]["decision"], "keep")
        self.assertEqual(args[2], destination)
        self.assertEqual(json.loads(out)["output"], str(destination))

    def test_plan_allows_future_target_names_and_explicit_plugin_loading(self):
        with patch.object(cli, "plan_bundle", return_value={"target": "future-platform"}) as planner:
            code, out, err = self.call("plan", str(self.base / "bundle"), "--target", "future-platform",
                                      "--release", "42", "--check-host", "--allow-plugins", "--format", "json")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["target"], "future-platform")
        self.assertEqual(planner.call_args.kwargs,
                         {"target": "future-platform", "release": "42", "check_host": True, "allow_external": True})

    def test_text_plan_preserves_arguments_and_reports_unresolved_dependencies(self):
        result = {"target": "arch", "release": None,
                  "install_argv": ["sudo", "pacman", "-S", "xorg-xrandr"],
                  "resources": [{"uri": "home://hello.sh", "action": "adapt", "reason": "Review display names"}],
                  "unresolved_dependencies": ["custom-command"], "warnings": ["Fixture warning"],
                  "host_checked": False}
        with patch.object(cli, "plan_bundle", return_value=result):
            code, out, err = self.call("plan", str(self.base / "bundle"), "--target", "arch")
        self.assertEqual(code, 0)
        self.assertIn('["sudo", "pacman", "-S", "xorg-xrandr"]', out)
        self.assertIn("adapt: home://hello.sh", out)
        self.assertIn("Unresolved dependencies: custom-command", out)
        self.assertIn("Plan only", out)
        self.assertEqual(err, "Warning: Fixture warning\n")

    def test_diff_and_snapshots_use_saved_data(self):
        self.persist("before")
        self.persist("after")
        code, out, _ = self.call("snapshots", "--state-dir", str(self.state), "--format", "json")
        self.assertEqual(code, 0)
        self.assertCountEqual(json.loads(out)["snapshots"], ["before", "after"])
        with patch.object(cli, "compare", return_value={"changed": []}) as comparer:
            code, out, err = self.call("diff", "before", "--state-dir", str(self.state), "--format", "json")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["changed"], [])
        self.assertEqual(comparer.call_args.args[0].id, "before")
        self.assertEqual(comparer.call_args.args[1].id, "after")

    def test_errors_are_stderr_only_and_argument_errors_return_two(self):
        code, out, err = self.call("list", "--state-dir", str(self.state), "--format", "json")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertNotIn("Traceback", err)
        self.assertTrue(err.startswith("driftstamp:"))
        for arguments in (("list", "--min-confidence", "101"), ("review", "scripts-hello"), ()):
            with self.subTest(arguments=arguments):
                code, out, err = self.call(*arguments)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertTrue(err)

    def test_help_and_version_succeed_without_scanning(self):
        with patch.object(cli, "scan_system") as scanner:
            for arguments in (("--help",), ("scan", "--help"), ("--version",)):
                with self.subTest(arguments=arguments):
                    code, out, err = self.call(*arguments)
                    self.assertEqual((code, err), (0, ""))
                    self.assertTrue(out)
            scanner.assert_not_called()

    @unittest.skipIf(os.name == "nt", "sudo account lookup is POSIX-specific")
    def test_sudo_resolves_original_user_without_scanning_host(self):
        from types import SimpleNamespace

        with patch.dict(os.environ, {"SUDO_UID": "12345", "HOME": "/root"}), \
                patch.object(os, "geteuid", return_value=0), \
                patch("pwd.getpwuid", return_value=SimpleNamespace(pw_dir=str(self.home))) as account:
            self.assertEqual(cli._invoking_home(), self.home)
            self.assertEqual(cli._default_state_dir(), self.home / ".local" / "state" / "driftstamp")
            account.assert_called_with(12345)


if __name__ == "__main__":
    unittest.main()
