"""Command-line interface for inspecting and carrying customizations forward."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from typing import Any

from . import __version__
from .context import ScanContext
from .engine import compare, safe_name, scan as scan_system
from .migration import export_bundle, plan_bundle
from .model import coverage
from .registry import load_modules, module_catalog
from .store import Store


DECISIONS = ("undecided", "keep", "skip", "investigate")


class UsageError(ValueError):
    """An invalid combination of otherwise parseable command arguments."""


def _confidence(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("confidence must be an integer from 0 to 100") from exc
    if not 0 <= number <= 100:
        raise argparse.ArgumentTypeError("confidence must be an integer from 0 to 100")
    return number


def _snapshot_name(value: str) -> str:
    try:
        return safe_name(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _version() -> str:
    return __version__


def _global_flags(parser: argparse.ArgumentParser, *, nested: bool = False) -> None:
    def default(value: Any) -> Any:
        return argparse.SUPPRESS if nested else value

    parser.add_argument("--state-dir", type=Path, default=default(None),
                        help="directory for snapshots and review decisions")
    parser.add_argument("--format", choices=("text", "json"), default=default("text"),
                        help="output format (default: text)")
    parser.add_argument("--no-color", action="store_true", default=default(False),
                        help="disable color (output is currently always plain text)")
    parser.add_argument("--allow-plugins", action="store_true", default=default(False),
                        help="load installed module and target plugins; executes trusted Python code")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="driftstamp",
        description="Discover forgotten customizations and prepare a reviewed migration bundle.",
        epilog="Evidence scores are not probabilities; coverage measures declared checks only.",
    )
    _global_flags(result)
    result.add_argument("--version", action="version", version=f"driftstamp {_version()}",
                        help="show the program version and exit")
    subparsers = result.add_subparsers(dest="command", required=True)

    def command(name: str, help_text: str) -> argparse.ArgumentParser:
        subparser = subparsers.add_parser(name, help=help_text, description=help_text)
        _global_flags(subparser, nested=True)
        return subparser

    scan = command("scan", "Discover customizations and save a new immutable snapshot.")
    scan.add_argument("--root", type=Path, help="filesystem root; a fixture root requires --home")
    scan.add_argument("--home", type=Path, help="physical home directory, located within --root")
    scan.add_argument("--platform", choices=("linux", "windows"),
                      default="windows" if os.name == "nt" else sys.platform,
                      help="platform to inspect (default: current platform)")
    scan.add_argument("--distro", help="distribution identifier (default: detect from root)")
    scan.add_argument("--scope", choices=("user", "system", "all"), default="all",
                      help="locations to inspect (default: all); omitted areas remain coverage gaps")
    scan.add_argument("--module", action="append", default=[], metavar="ID",
                      help="run this module; repeat to select more")
    scan.add_argument("--include", action="append", type=Path, default=[], metavar="PATH",
                      help="additional script discovery location; repeatable")
    scan.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                      help="exclude matching paths; quote shell globs; repeatable")
    scan.add_argument("--name", type=_snapshot_name, help="name for the saved snapshot")

    listing = command("list", "List findings from a saved scan without rescanning.")
    listing.add_argument("--scan", default="latest", metavar="NAME", help="saved snapshot to read (default: latest)")
    listing.add_argument("--module", action="append", default=[], metavar="ID", help="show findings from this module; repeatable")
    listing.add_argument("--decision", choices=DECISIONS, help="show findings with this migration decision")
    listing.add_argument("--min-confidence", type=_confidence, default=0, metavar="0..100",
                         help="minimum uncalibrated evidence score (default: 0)")
    listing.add_argument("--needs-review", action="store_true",
                         help="show undecided or investigate findings")

    show = command("show", "Explain one finding, with its files, evidence, dependencies, and warnings.")
    show.add_argument("id", metavar="ID", help="finding ID from list")
    show.add_argument("--scan", default="latest", metavar="NAME", help="saved snapshot to read (default: latest)")

    review = command("review", "Record a migration decision and optional note for one finding.")
    review.add_argument("id", metavar="ID", help="finding ID in the latest snapshot")
    review.add_argument("--decision", required=True, choices=DECISIONS,
                        help="migration decision to save; skip does not hide a discovery finding")
    review.add_argument("--note", help="why this customization exists or should be kept")

    diff = command("diff", "Compare saved snapshots, accounting for unscanned areas.")
    diff.add_argument("old", metavar="OLD", help="earlier saved snapshot name")
    diff.add_argument("new", nargs="?", default="latest", metavar="NEW", help="later snapshot name (default: latest)")

    score = command("coverage", "Show declared-check coverage and every coverage gap.")
    score.add_argument("--scan", default="latest", metavar="NAME", help="saved snapshot to read (default: latest)")

    command("modules", "List available discovery modules and platform support.")

    export = command("export", "Export findings marked keep; verify their files have not changed.")
    export.add_argument("--scan", default="latest", metavar="NAME", help="saved snapshot to export (default: latest)")
    export.add_argument("--output", required=True, type=Path, metavar="PATH", help="new bundle directory; must not already exist")

    plan = command("plan", "Inspect a migration bundle and print setup instructions; never apply them.")
    plan.add_argument("bundle", type=Path, metavar="BUNDLE", help="exported bundle directory containing manifest.json")
    plan.add_argument("--target", required=True,
                      help="destination adapter (built in: ubuntu, arch; plugins may add more)")
    plan.add_argument("--release", help="destination release identifier")
    plan.add_argument("--check-host", action="store_true",
                      help="also inspect the current destination host for conflicts")

    command("snapshots", "List saved snapshot names.")
    return result


def _invoking_home() -> Path:
    # Resolve a sudo caller through the account database instead of inheriting root's HOME.
    if os.name != "nt" and getattr(os, "geteuid", lambda: -1)() == 0 and "SUDO_UID" in os.environ:
        import pwd

        try:
            return Path(pwd.getpwuid(int(os.environ["SUDO_UID"])).pw_dir)
        except (ValueError, KeyError) as exc:
            raise UsageError("cannot resolve the original sudo user; pass --home explicitly") from exc
    return Path.home()


def _default_state_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        return (Path(base) if base else _invoking_home() / "AppData" / "Local") / "driftstamp"
    # A sudo run must not borrow a state directory inherited from a different account.
    if getattr(os, "geteuid", lambda: -1)() == 0 and "SUDO_UID" in os.environ:
        return _invoking_home() / ".local" / "state" / "driftstamp"
    base = os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base else _invoking_home() / ".local" / "state") / "driftstamp"


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _detect_distro(root: Path, platform: str) -> str:
    if platform != "linux":
        return "unknown"
    candidate = root / "etc" / "os-release"
    try:
        if not candidate.resolve().is_relative_to(root.resolve()):
            return "unknown"
        with candidate.open(encoding="utf-8") as stream:
            contents = stream.read(65536)
    except (OSError, UnicodeError, RuntimeError):
        return "unknown"
    for line in contents.splitlines():
        if line.startswith("ID="):
            return line[3:].strip().strip("\"'") or "unknown"
    return "unknown"


def _context(args: argparse.Namespace) -> ScanContext:
    native_root = Path(Path.cwd().anchor)
    root = _absolute(args.root) if args.root is not None else native_root
    if not root.is_dir():
        raise UsageError(f"scan root does not exist or is not a directory: {root}")
    if root != native_root and args.home is None:
        raise UsageError("a fixture --root requires an explicit --home within that root")
    home = _absolute(args.home if args.home is not None else _invoking_home())
    try:
        if not home.is_relative_to(root) or not home.resolve().is_relative_to(root.resolve()):
            raise UsageError("--home must be located within --root")
        return ScanContext(
            root=root, home=home, platform=args.platform,
            distro=args.distro or _detect_distro(root, args.platform), scope=args.scope,
            includes=tuple(_absolute(path) for path in args.include), excludes=tuple(args.exclude),
        )
    except (ValueError, OSError, RuntimeError) as exc:
        raise UsageError(str(exc)) from exc


def _emit_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))


def _score_text(score: dict[str, Any]) -> str:
    percent = score.get("percent")
    label = "n/a" if percent is None else f"{percent:g}%"
    return f"Declared-check coverage: {label} ({score['completed']}/{score['total']} complete)"


def _render_value(value: Any, indent: int = 0) -> None:
    """Readable fallback for extensible plugin-produced report dictionaries."""
    prefix = " " * indent
    if isinstance(value, dict):
        for key, item in value.items():
            title = key.replace("_", " ").capitalize()
            if isinstance(item, (dict, list, tuple)) and item:
                print(f"{prefix}{title}:")
                _render_value(item, indent + 2)
            elif isinstance(item, (dict, list, tuple)):
                print(f"{prefix}{title}: none")
            else:
                print(f"{prefix}{title}: {item if item is not None else 'unknown'}")
    elif isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, (dict, list, tuple)):
                _render_value(item, indent + 2)
            else:
                print(f"{prefix}- {item}")
    else:
        print(f"{prefix}{value}")


def _report(value: Any, args: argparse.Namespace) -> None:
    if args.format == "json":
        _emit_json(value)
    else:
        _render_value(value)


def _plan_text(result: dict[str, Any]) -> None:
    print(f"Destination: {result.get('target', 'unknown')}"
          + (f" {result['release']}" if result.get("release") else ""))
    print("Plan only; no commands or file changes have been applied.")
    print(f"Current host inspected: {'yes' if result.get('host_checked') else 'no'}")
    if result.get("source"):
        _render_value({"source": result["source"]})
    if result.get("install_argv"):
        print("Installation arguments (review before execution):")
        print("  " + json.dumps(result["install_argv"], ensure_ascii=False))
    elif result.get("packages"):
        print("Packages: " + ", ".join(result["packages"]))
    resources = result.get("resources", [])
    print(f"Resources: {len(resources)}")
    for resource in resources:
        print(f"  {resource.get('action', 'review')}: {resource.get('uri', 'unknown')}")
        if resource.get("reason"):
            print(f"    {resource['reason']}")
    unresolved = result.get("unresolved_dependencies", [])
    print("Unresolved dependencies: " + (", ".join(unresolved) if unresolved else "none declared"))
    for warning in result.get("warnings", []):
        print(f"Warning: {warning}", file=sys.stderr)
    known = {"target", "release", "source", "packages", "install_argv", "resources",
             "unresolved_dependencies", "warnings", "host_checked"}
    _render_value({key: value for key, value in result.items() if key not in known})


def _review_for(reviews: dict[str, Any], finding_id: str) -> dict[str, Any]:
    return reviews.get(finding_id, {"decision": "undecided", "note": None})


def _run(args: argparse.Namespace) -> int:
    if args.command == "modules":
        result = {
            "modules": module_catalog(allow_external=args.allow_plugins),
            "notes": [
                "Built-in discovery currently targets Linux customization files.",
                "Windows cmd/PowerShell discovery and dedicated RHEL support are planned, not implemented.",
                "External plugins are disabled unless --allow-plugins is supplied.",
            ],
        }
        _report(result, args)
        return 0

    if args.command == "plan":
        result = plan_bundle(args.bundle.expanduser(), target=args.target, release=args.release,
                             check_host=args.check_host, allow_external=args.allow_plugins)
        if args.format == "json":
            _emit_json(result)
        else:
            _plan_text(result)
        return 0

    store = Store(args.state_dir.expanduser() if args.state_dir is not None else _default_state_dir())

    if args.command == "scan":
        ctx = _context(args)
        modules, warnings = load_modules(allow_external=args.allow_plugins)
        unknown = sorted(set(args.module) - {module.spec.id for module in modules})
        if unknown:
            raise UsageError(f"unknown module(s): {', '.join(unknown)}; see 'driftstamp modules'")
        snapshot = scan_system(ctx, modules=modules, selected=args.module or None,
                               name=args.name, warnings=warnings)
        store.save(snapshot)
        score = coverage(snapshot.checks)
        if args.format == "json":
            _emit_json({"snapshot": asdict(snapshot), "coverage": score})
        else:
            print(f"Saved scan: {snapshot.id}")
            print(f"Findings: {len(snapshot.findings)}")
            print(_score_text(score))
            print(score["interpretation"])
            for check in snapshot.checks:
                if check.status not in {"complete", "not_applicable"}:
                    print(f"  {check.status}: {check.id} — {check.detail}")
            for warning in snapshot.warnings:
                print(f"Warning: {warning}", file=sys.stderr)
        return 3 if any(check.status in {"partial", "blocked"} for check in snapshot.checks) else 0

    if args.command == "snapshots":
        names = store.snapshots()
        if args.format == "json":
            _emit_json({"snapshots": names})
        else:
            print("\n".join(names) if names else "No saved scans. Run 'driftstamp scan' first.")
        return 0

    if args.command == "review":
        result = store.review(args.id, args.decision, note=args.note)
        _report({"id": args.id, "review": result}, args)
        return 0

    if args.command == "diff":
        _report(compare(store.load(args.old), store.load(args.new)), args)
        return 0

    snapshot = store.load(args.scan)
    if args.command == "coverage":
        score = coverage(snapshot.checks)
        if args.format == "json":
            _emit_json({"scan": snapshot.id, "coverage": score,
                        "checks": [asdict(check) for check in snapshot.checks],
                        "warnings": snapshot.warnings})
        else:
            print(f"Scan: {snapshot.id}")
            print(_score_text(score))
            print(score["interpretation"])
            for check in snapshot.checks:
                print(f"  {check.status}: {check.id} — {check.detail}")
            for warning in snapshot.warnings:
                print(f"Warning: {warning}", file=sys.stderr)
        return 0

    reviews = store.reviews()
    if args.command == "export":
        result = export_bundle(snapshot, reviews, args.output.expanduser())
        if args.format == "json":
            _emit_json(result)
        else:
            print(f"Exported bundle: {_absolute(args.output)}")
            print(f"Findings: {len(result.get('findings', []))}")
            print(f"Resources: {len(result.get('files', []))}")
            for warning in result.get("warnings", []):
                print(f"Warning: {warning}", file=sys.stderr)
        return 0

    if args.command == "show":
        finding = next((item for item in snapshot.findings if item.id == args.id), None)
        if finding is None:
            raise ValueError(f"finding {args.id!r} does not exist in scan {snapshot.id!r}")
        review = _review_for(reviews, finding.id)
        if args.format == "json":
            _emit_json({"scan": snapshot.id, "finding": asdict(finding), "review": review})
        else:
            print(f"{finding.title} [{finding.id}]")
            print(finding.summary)
            print(f"Module: {finding.module}")
            print(f"Evidence strength: {finding.confidence}/100 (not a probability)")
            print(f"Migration: {finding.migration}")
            print(f"Decision: {review['decision']}")
            if review.get("note"):
                print(f"Your note: {review['note']}")
            _render_value({"resources": [asdict(resource) for resource in finding.resources],
                           "evidence": finding.evidence, "dependencies": finding.dependencies,
                           "warnings": finding.warnings})
        return 0

    findings = []
    for finding in snapshot.findings:
        review = _review_for(reviews, finding.id)
        if args.module and finding.module not in args.module:
            continue
        if args.decision and review["decision"] != args.decision:
            continue
        if finding.confidence < args.min_confidence:
            continue
        if args.needs_review and review["decision"] not in {"undecided", "investigate"}:
            continue
        findings.append({**asdict(finding), "review": review})
    if args.format == "json":
        _emit_json({"scan": snapshot.id, "findings": findings})
    else:
        print(f"Scan: {snapshot.id} — {len(findings)} finding(s)")
        for finding in findings:
            print(f"{finding['id']}  {finding['module']}  {finding['confidence']}/100  "
                  f"{finding['review']['decision']}  {finding['title']}")
        if findings:
            print("Scores show evidence strength, not probability. Use 'driftstamp show ID' for details.")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        return _run(args)
    except UsageError as exc:
        print(f"driftstamp: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0
    except (OSError, ValueError) as exc:
        print(f"driftstamp: {exc}", file=sys.stderr)
        return 1
