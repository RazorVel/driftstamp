"""Reproducible manual generation; optional groff validation when available."""

import argparse
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from driftstamp import __version__, cli, manpage


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_builder():
    spec = importlib.util.spec_from_file_location("driftstamp_build_manpage",
                                                PROJECT_ROOT / "tools" / "build_manpage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ManpageTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("groff"), "Optional groff formatter is not installed")
    def test_page_renders_without_formatter_warnings(self):
        result = subprocess.run([shutil.which("groff"), "-ww", "-Tutf8", "-man"],
                                input=manpage.render_manpage(), capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn("DRIFTSTAMP", result.stdout)

    def test_checked_in_page_matches_parser_and_source_version(self):
        rendered = manpage.render_manpage()
        self.assertEqual((PROJECT_ROOT / "man" / "driftstamp.1").read_bytes(),
                         rendered.encode("utf-8"))
        self.assertIn(f'"Driftstamp {__version__}"', rendered)
        self.assertEqual(rendered, manpage.render_manpage())

    def test_required_sections_commands_flags_and_positionals_are_documented(self):
        parser = cli.parser()
        rendered = manpage.render_manpage(parser)
        for section in ("NAME", "SYNOPSIS", "DESCRIPTION", "GLOBAL OPTIONS", "COMMANDS", "EXAMPLES",
                        "FILES", "ENVIRONMENT", "EXIT STATUS", "CONFIDENCE", "LIMITATIONS", "PLUGINS",
                        "LICENSE", "SEE ALSO"):
            self.assertIn(f'.SH "{section}"', rendered)
        commands = next(action for action in parser._actions
                        if isinstance(action, argparse._SubParsersAction))
        global_section = rendered.split('.SH "GLOBAL OPTIONS"', 1)[1].split('.SH "COMMANDS"', 1)[0]
        for name, child in commands.choices.items():
            self.assertIn(f'.SS "{name}"', rendered)
            section = rendered.split(f'.SS "{name}"', 1)[1].split(".SS ", 1)[0].split(".SH ", 1)[0]
            for action in child._actions:
                if action.help == argparse.SUPPRESS:
                    continue
                if action.option_strings:
                    for flag in action.option_strings:
                        self.assertIn(manpage.roff_text(flag), section + global_section)
                else:
                    self.assertIn(manpage.roff_text(action.metavar or action.dest.upper()), section)

    def test_parser_changes_appear_without_manual_option_lists(self):
        parser = cli.parser()
        subparsers = next(action for action in parser._actions
                         if isinstance(action, argparse._SubParsersAction))
        future = subparsers.add_parser("future", description="Inspect a future platform.")
        future.add_argument("resource", metavar="RESOURCE", help="Resource to inspect")
        future.add_argument("--provider", action="append", choices=("one", "two"),
                            required=True, help="Select a provider")
        rendered = manpage.render_manpage(parser)
        section = rendered.split('.SS "future"', 1)[1].split('.SH "EXAMPLES"', 1)[0]
        for expected in ("RESOURCE", r"\-\-provider", "Select a provider.", "Required.",
                         "Repeatable.", "Choices: one, two."):
            self.assertIn(expected, section)

    def test_hidden_actions_stay_hidden_and_nested_commands_are_included(self):
        parser = cli.parser()
        parser.add_argument("--internal-only", help=argparse.SUPPRESS)
        subparsers = next(action for action in parser._actions
                         if isinstance(action, argparse._SubParsersAction))
        child = subparsers.add_parser("future", description="Future command.")
        nested = child.add_subparsers(dest="nested")
        nested.add_parser("inspect", description="Nested inspection.")
        rendered = manpage.render_manpage(parser)
        self.assertNotIn("internal", rendered)
        self.assertIn('.SS "future inspect"', rendered)

    def test_roff_text_blocks_request_and_escape_injection(self):
        text = '.sy touch /tmp/unwanted\n\'sy command\n\\*[danger] "quoted" --flag\n\x00'
        escaped = manpage.roff_text(text)
        self.assertEqual(escaped,
                         '\\&.sy touch /tmp/unwanted\n\\&\'sy command\n'
                         '\\e*[danger] \\(dqquoted\\(dq \\-\\-flag\n?')
        parser = argparse.ArgumentParser(prog='tool"\n.sy unexpected', description=text)
        parser.add_argument("--test", help=text)
        page = manpage.render_manpage(parser, version='1"\n.sy version')
        self.assertNotIn("\n.sy", page)
        self.assertNotIn("\n'sy", page)
        self.assertIn(r"\e*[danger]", page)
        self.assertIn(r"\&.sy", page)

    def test_generation_is_independent_of_home_platform_locale_and_terminal(self):
        pages = []
        for platform, columns, locale, home in (("linux", "40", "C", "/private/alice"),
                                                ("win32", "200", "tr_TR.UTF-8", "/private/bob")):
            with patch.dict(os.environ, {"HOME": home, "USERPROFILE": home,
                                         "XDG_STATE_HOME": home + "/state", "COLUMNS": columns,
                                         "LC_ALL": locale, "SOURCE_DATE_EPOCH": columns}, clear=True), \
                    patch.object(sys, "platform", platform):
                pages.append(manpage.render_manpage())
        self.assertEqual(pages[0], pages[1])
        self.assertNotIn("/private/", pages[0])
        self.assertNotIn("SOURCE_DATE_EPOCH", pages[0])

    def test_rendering_never_scans_invokes_commands_or_reads_host_defaults(self):
        def denied(*args, **kwargs):
            self.fail("Manual rendering attempted host inspection or command execution")

        with patch.object(cli, "scan_system", side_effect=denied), \
                patch.object(cli, "load_modules", side_effect=denied), \
                patch.object(cli, "module_catalog", side_effect=denied), \
                patch.object(cli, "_invoking_home", side_effect=denied), \
                patch.object(cli, "_detect_distro", side_effect=denied), \
                patch.object(Path, "home", side_effect=denied), \
                patch.object(subprocess, "run", side_effect=denied), \
                patch.object(subprocess, "Popen", side_effect=denied), \
                patch.object(os, "system", side_effect=denied):
            self.assertIn('.SH "COMMANDS"', manpage.render_manpage())

    def test_build_and_check_detect_missing_stale_and_parser_changed_pages(self):
        builder = load_builder()
        with tempfile.TemporaryDirectory(prefix="driftstamp-man-") as temporary:
            output = Path(temporary) / "man" / "driftstamp.1"
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(builder.main(["--check", "--output", str(output)]), 1)
                self.assertFalse(output.exists())
                self.assertEqual(builder.main(["--output", str(output)]), 0)
                original = output.read_bytes()
                self.assertEqual(builder.main(["--check", "--output", str(output)]), 0)
                output.write_bytes(b"old manual\n")
                self.assertEqual(builder.main(["--check", "--output", str(output)]), 1)
                self.assertEqual(output.read_bytes(), b"old manual\n")
                output.write_bytes(original)
                changed = cli.parser()
                changed.add_argument("--new-option", help="A newly added option")
                with patch.object(manpage, "parser", return_value=changed):
                    self.assertEqual(builder.main(["--check", "--output", str(output)]), 1)
                with patch.object(manpage, "__version__", "99.0.0"):
                    self.assertEqual(builder.main(["--check", "--output", str(output)]), 1)
            self.assertIn("missing or stale", stderr.getvalue())
            self.assertIn("up to date", stdout.getvalue())

    def test_builder_reports_write_error_without_traceback(self):
        builder = load_builder()
        with tempfile.TemporaryDirectory(prefix="driftstamp-man-") as temporary:
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(builder.main(["--output", temporary]), 1)
            self.assertIn("Cannot write manual:", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_direct_build_tool_works_outside_checkout_without_pythonpath(self):
        with tempfile.TemporaryDirectory(prefix="driftstamp-man-") as temporary:
            output = Path(temporary) / "manual.1"
            env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
            result = subprocess.run([sys.executable, str(PROJECT_ROOT / "tools" / "build_manpage.py"),
                                     "--output", str(output)], cwd=temporary, env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_bytes(), manpage.render_manpage().encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
