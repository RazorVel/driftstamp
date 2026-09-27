"""Generate the reference manual from argparse without inspecting a machine.

Only command structure and help text are taken from argparse. Runtime defaults
(home paths, platform, environment and installed-package metadata) are not
rendered. Keep behavioral prose here and regenerate the checked-in man page
whenever behavior or command help changes.
"""

from __future__ import annotations

import argparse
import re

from . import __version__
from .cli import parser


def roff_text(value: object) -> str:
    """Escape literal text, including request-looking lines and escape sequences."""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "?", text)
    text = text.replace("\t", "    ").replace("\\", r"\e")
    text = text.replace("-", r"\-").replace('"', r"\(dq")
    return "\n".join(r"\&" + line if line.startswith((".", "'")) else line
                     for line in text.split("\n"))


def _argument(value: object) -> str:
    """Produce one quoted macro argument; literal newlines cannot create macros."""
    return '"' + roff_text(" ".join(str(value).split())) + '"'


def _actions(command: argparse.ArgumentParser):
    # argparse has no public API for enumerating actions. This single adapter is
    # intentionally small and covered by parser-change regression tests.
    return [action for action in command._actions
            if action.help != argparse.SUPPRESS
            and not isinstance(action, argparse._SubParsersAction)]


def _commands(command: argparse.ArgumentParser, prefix: tuple[str, ...] = ()):
    for action in command._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, child in action.choices.items():
                path = (*prefix, name)
                yield path, child
                yield from _commands(child, path)


def _value_label(action: argparse.Action) -> str:
    if action.nargs == 0:
        return ""
    if action.metavar is not None:
        labels = action.metavar if isinstance(action.metavar, tuple) else (action.metavar,)
    elif action.choices is not None:
        labels = ("{" + ",".join(map(str, action.choices)) + "}",)
    else:
        labels = (action.dest.upper(),)
    value = str(labels[0])
    if action.nargs == "?":
        return f"[{value}]"
    if action.nargs == "*":
        return f"[{value} ...]"
    if action.nargs == "+":
        return f"{value} [{value} ...]"
    if isinstance(action.nargs, int):
        return " ".join(str(labels[min(index, len(labels) - 1)])
                        for index in range(action.nargs))
    if action.nargs in (argparse.REMAINDER, argparse.PARSER):
        return f"{value} ..."
    return value


def _label(action: argparse.Action, *, aliases: bool = True) -> str:
    value = _value_label(action)
    if not action.option_strings:
        return value
    flags = ", ".join(action.option_strings) if aliases else action.option_strings[-1]
    return flags + (" " + value if value else "")


def _help(action: argparse.Action, program: str) -> str:
    # No %(default)s expansion: it could disclose a host-sensitive runtime value.
    help_text = (action.help or "").replace("%(prog)s", program).strip()
    if not help_text:
        help_text = "Command argument." if not action.option_strings else "Command option."
    if not help_text.endswith((".", "!", "?")):
        help_text += "."
    additions = []
    if action.required and action.option_strings:
        additions.append("Required.")
    if (isinstance(action, (argparse._AppendAction, argparse._AppendConstAction, argparse._ExtendAction))
            and not re.search(r"\brepeat(?:able)?\b", help_text, re.IGNORECASE)):
        additions.append("Repeatable.")
    if action.choices is not None:
        additions.append("Choices: " + ", ".join(map(str, action.choices)) + ".")
    return " ".join([help_text, *additions])


class _Page:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def macro(self, name: str, *arguments: object) -> None:
        self.lines.append("." + name + (" " + " ".join(_argument(arg) for arg in arguments)
                                        if arguments else ""))

    def paragraph(self, text: str) -> None:
        self.macro("PP")
        self.lines.append(roff_text(text))

    def item(self, label: str, text: str) -> None:
        self.macro("TP")
        self.macro("B", label)
        self.lines.append(roff_text(text))

    def section(self, name: str, *paragraphs: str) -> None:
        self.macro("SH", name)
        for text in paragraphs:
            self.paragraph(text)


def render_manpage(command_parser: argparse.ArgumentParser | None = None, *,
                   version: str | None = None) -> str:
    """Return deterministic UTF-8 roff source from the current CLI definition."""
    command_parser = parser() if command_parser is None else command_parser
    version = __version__ if version is None else version
    page = _Page()
    page.lines.append('.\\" Generated by tools/build_manpage.py; edit driftstamp/manpage.py or CLI help.')
    # An empty date avoids current-time, locale and SOURCE_DATE_EPOCH differences.
    page.macro("TH", "DRIFTSTAMP", "1", "", f"Driftstamp {version}", "User Commands")
    page.macro("nh")
    # Unlike man macros, this troff request takes an unquoted adjustment mode.
    page.lines.append(".ad l")
    page.section("NAME")
    page.lines.append(roff_text("driftstamp - rediscover machine customizations and prepare reviewed migrations"))
    page.section("SYNOPSIS")
    page.macro("B", "driftstamp [GLOBAL OPTIONS] COMMAND [ARGUMENTS]")
    page.macro("br")
    page.macro("B", "driftstamp --help")
    page.macro("br")
    page.macro("B", "driftstamp --version")
    page.section("DESCRIPTION",
                 command_parser.description or "Inspect machine customization candidates.",
                 "Driftstamp reads supported configuration files and literal references, saves "
                 "immutable snapshots, and records your migration decisions separately. The initial "
                 "discovery modules cover Linux i3, Bash/Zsh startup files, local text scripts, "
                 "systemd units and drop-ins, and cron. Built-in destination adapters propose "
                 "Ubuntu or Arch Linux setup steps.",
                 "Discovery does not execute scripts, source shell configuration, query active "
                 "services, or change inspected source files. State and export commands write only "
                 "their requested local outputs. A migration plan is a review document; it never "
                 "installs packages, activates services, or applies destination changes.",
                 "Without an installation baseline, candidates and references cannot establish "
                 "who authored a file or reconstruct deleted changes. Use saved snapshots to "
                 "compare later observations.")
    page.section("GLOBAL OPTIONS",
                 "--state-dir, --format, --no-color, and --allow-plugins are accepted before or "
                 "after a subcommand. If repeated, the later value wins. --version is accepted "
                 "at the top level. --help describes the parser where it appears.")
    root_actions = _actions(command_parser)
    for action in root_actions:
        page.item(_label(action), _help(action, command_parser.prog))
    shared_options = {tuple(action.option_strings) for action in root_actions if action.option_strings}

    page.section("COMMANDS",
                 "Each command accepts the common global options above. Saved-scan readers use "
                 "the latest completed snapshot by default and never silently rescan.")
    details = {
        "scan": "All applicable built-in modules and both user and system locations are selected "
                "by default. Access failures and unresolved references remain visible in the "
                "coverage report. Snapshot names are immutable: use a new name for a later scan. "
                "An alternate filesystem root requires --home within that root. User and all "
                "scopes require an existing home directory. Directory exclusions also block "
                "referenced children and resolved symlink targets. Explicit includes "
                "add script locations and do not select files for migration. Quote exclusion globs "
                "to prevent expansion by your shell.",
        "list": "Use the finding IDs printed by this command with show and review. Confidence "
                "filters select by evidence strength, not probability. --needs-review includes "
                "undecided and investigate findings; other filters are combined with it.",
        "show": "Displays the finding's resources, fingerprints, evidence, dependencies, warnings, "
                "migration hint, decision and note. This command does not print raw file contents "
                "or a vendor-baseline diff.",
        "review": "The finding must exist in the latest snapshot. Decisions and notes persist "
                  "across scans for the same stable finding ID. Omitting --note preserves an "
                  "existing note; an empty string clears it. keep selects a finding for export; "
                  "skip leaves it in the inventory without selecting it for export. undecided "
                  "and investigate remain reviewable.",
        "diff": "Compares saved fingerprints, file modes, links and discovery scope. Areas that "
                "were not inspected are reported as comparison gaps rather than assumed deletions.",
        "coverage": "Lists every declared check and its status, including unsupported, excluded, "
                    "blocked and partial areas. See CONFIDENCE for the scoring formula.",
        "modules": "Lists available discovery module IDs, their platform support and declared "
                   "checks. Installed external modules are loaded only with --allow-plugins.",
        "export": "Exports only findings currently marked keep and verifies each source's content "
                  "or link target and mode against the selected snapshot. Missing or changed sources "
                  "fail visibly; rescan and review before trying again. The output directory must "
                  "not already exist. An empty selection is an error. The bundle includes selected "
                  "content, hashes, mode and link metadata, evidence and notes. Links are recorded, "
                  "not created. Contents are not redacted and may contain credentials.",
        "plan": "Validates the bundle and proposes package arguments and copy, adapt, merge or "
                "review or omit actions. Unknown dependencies remain unresolved. --release records the "
                "requested release; built-in package mappings are not release-specific and package "
                "repository availability is not verified. --check-host also inspects the current "
                "destination for platform compatibility and file conflicts. No plan is applied.",
        "snapshots": "Prints saved snapshot names in sorted order; this does not inspect source files.",
    }
    for path, child in _commands(command_parser):
        name = " ".join(path)
        page.macro("SS", name)
        own_actions = [action for action in _actions(child)
                       if tuple(action.option_strings) not in shared_options or not action.option_strings]
        synopsis = [command_parser.prog, *path, "[GLOBAL OPTIONS]"]
        for action in own_actions:
            label = _label(action, aliases=False)
            synopsis.append(label if not action.option_strings or action.required else f"[{label}]")
        page.macro("B", " ".join(synopsis))
        page.paragraph(child.description or "Run this command.")
        if name in details:
            page.paragraph(details[name])
        for action in own_actions:
            page.item(_label(action), _help(action, child.prog))

    page.section("EXAMPLES", "Discover, inspect, and select a finding. Replace FINDING_ID with an "
                 "actual ID from list.")
    page.macro("nf")
    for line in (
        "driftstamp scan --name before-cleanup",
        "driftstamp list --needs-review",
        "driftstamp show FINDING_ID",
        'driftstamp review FINDING_ID --decision keep --note "Needed for my desk"',
        "driftstamp export --output ./my-setup",
        "driftstamp plan ./my-setup --target arch",
    ):
        page.lines.append(roff_text(line))
    page.macro("fi")
    page.paragraph("Limit discovery and produce structured output:")
    page.macro("nf")
    for line in (
        "driftstamp scan --scope user --module i3 --module systemd",
        "driftstamp scan --include ~/scripts --exclude '~/scripts/old/**'",
        "driftstamp list --format json",
        "driftstamp coverage --scan before-cleanup",
        "driftstamp diff before-cleanup latest",
    ):
        page.lines.append(roff_text(line))
    page.macro("fi")
    page.paragraph("Inspect an isolated fixture without inspecting your real home:")
    page.macro("nf")
    page.lines.append(roff_text("driftstamp --state-dir ./fixture-state scan --root ./fixture/root --home ./fixture/root/home/alice --distro ubuntu"))
    page.macro("fi")

    page.section("INSTALLATION",
                 "The source distribution includes tools/install_user.py. Pass a trusted built "
                 "Driftstamp wheel to install into a private POSIX environment under "
                 "~/.local/share/driftstamp, expose ~/.local/bin/driftstamp, and install this "
                 "manual under ~/.local/share/man/man1. --prefix selects another user prefix. "
                 "The command directory must be on PATH. The installer verifies a new environment "
                 "before switching the command link and retains earlier environments. See README.md "
                 "for offline build prerequisites and installation commands.")
    page.section("FILES", "--state-dir overrides the state directory. Paths below are symbolic, "
                 "not values obtained from the build machine.")
    page.item("$XDG_STATE_HOME/driftstamp/", "Default Linux state directory when XDG_STATE_HOME is set. "
              "Otherwise use ~/.local/state/driftstamp/. Under sudo, use the original caller's "
              "~/.local/state/driftstamp/ and ignore an inherited XDG_STATE_HOME.")
    page.item("%LOCALAPPDATA%/driftstamp/", "Default Windows state directory; falls back to "
              "~/AppData/Local/driftstamp/ when LOCALAPPDATA is unset. Native Windows discovery "
              "is not implemented by the built-in modules.")
    page.item("STATE_DIR/snapshots/NAME.json", "Versioned immutable snapshot records.")
    page.item("STATE_DIR/latest.json", "Reference to the latest saved snapshot.")
    page.item("STATE_DIR/reviews.json", "Persistent decisions and notes keyed by finding ID.")
    page.item("BUNDLE/manifest.json", "Versioned export manifest, selected findings and resource metadata.")
    page.item("BUNDLE/blobs/SHA256", "Bundled regular-file contents addressed by their SHA-256 digest.")

    page.section("ENVIRONMENT")
    page.item("XDG_STATE_HOME", "Linux state base; ignored for a sudo caller unless --state-dir is explicit.")
    page.item("LOCALAPPDATA", "Windows state base.")
    page.item("HOME, USERPROFILE", "Used by Python's platform-specific home-directory lookup when "
              "--home is omitted. A sudo caller is resolved through the account database instead.")
    page.item("SUDO_UID", "On a privileged POSIX invocation, identifies the original user for home "
              "and default-state selection. If the account cannot be resolved, pass --home and "
              "--state-dir explicitly.")
    page.paragraph("The built-in collectors use conventional configuration locations. Custom "
                   "XDG_CONFIG_HOME and ZDOTDIR layouts are not automatically discovered.")

    page.section("EXIT STATUS")
    page.item("0", "Operation completed, including --help and --version. Unsupported or explicitly "
              "excluded checks alone do not change this status.")
    page.item("1", "Runtime, filesystem, snapshot or bundle-data failure.")
    page.item("2", "Invalid command-line arguments or argument combinations.")
    page.item("3", "A scan was saved, but at least one declared check is blocked or partial.")
    page.paragraph("Reports go to standard output; diagnostics go to standard error. --format json "
                   "keeps successful report output machine-readable. A successful exit code is not "
                   "a claim that every customization was found.")

    page.section("CONFIDENCE",
                 "Finding confidence is an uncalibrated evidence-strength score from 0 to 100. "
                 "Inspect the associated evidence and warnings. It is not a probability of human "
                 "authorship, completeness or safe migration.",
                 "Declared-check coverage is 100 times the sum of completed check weights divided "
                 "by the sum of all applicable check weights. not_applicable checks are omitted "
                 "from the denominator. Partial, blocked, unsupported and excluded checks remain "
                 "in the denominator and receive no completion credit. An empty denominator is "
                 "reported as unavailable, not 100 percent.",
                 "Software test results are a separate measurement. Passing reproducible tests "
                 "does not measure recall on real machines or prove a migration will succeed.")
    page.section("LIMITATIONS",
                 "Current discovery follows supported literal references without evaluating shell "
                 "expressions, i3 variables, arbitrary script bodies or active service state. Some "
                 "dependencies remain unresolved. Regular-file reads are limited to 1 MiB and "
                 "directory traversal to 2,000 entries per discovery root. Directory symlinks are "
                 "not traversed. Failures and scope gaps remain visible.",
                 "Package configuration baselines, arbitrary application preferences, ACLs, "
                 "capabilities and ownership comparison remain unsupported. A configuration "
                 "candidate does not prove a modification from its packaged default. Native "
                 "PowerShell, CMD, Windows services/tasks and dedicated RPM/Red Hat collectors "
                 "require future modules.",
                 "Migration is deliberately reviewed. Hardware identifiers, usernames, paths, "
                 "credentials and distribution defaults can differ on the destination. Exported "
                 "content is local and unredacted; review it before sharing. This release does "
                 "not implement unattended restoration or continuous background monitoring.")
    page.section("PLUGINS",
                 "Discovery modules use the driftstamp.modules Python entry-point group; "
                 "destination adapters use driftstamp.targets. External plugins are disabled "
                 "unless --allow-plugins is supplied. Enabling them executes their Python code "
                 "with the same privileges as Driftstamp; load only trusted packages.",
                 "Plugins declare an API version and capabilities. Discovery module failures "
                 "and unsupported areas must remain visible rather than implying successful "
                 "inspection. A new discovery resource kind also needs explicit export and "
                 "planning support before it can be migrated. See docs/PLUGINS.md in the source "
                 "distribution for the contract and the Red Hat extension example.")
    page.section("LICENSE", "Driftstamp is distributed under the MIT License. See LICENSE in the "
                 "source distribution for the full permission notice and warranty disclaimer.")
    page.section("SEE ALSO")
    page.lines.append(roff_text("i3(1), bash(1), zsh(1), systemd.unit(5), crontab(5)"))
    page.paragraph("README.md, docs/PLUGINS.md and docs/COVERAGE.md in the source distribution. "
                   "Use driftstamp COMMAND --help for command-specific usage.")
    return "\n".join(page.lines) + "\n"
