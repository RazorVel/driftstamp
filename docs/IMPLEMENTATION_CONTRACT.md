# Driftstamp API v1

Python >=3.11. No runtime or test third-party dependencies. Standard-library unittest.
This document describes the implemented extension and command interfaces. See
README.md for supported scope and known limitations.

## Public Python API (v1)

Dataclasses in `driftstamp.model` (all fields JSON-serializable via dataclasses.asdict):

* `Resource(uri: str, path: str, sha256: str | None = None, mode: int | None = None, symlink: str | None = None, kind: str = "file")`. URI is stable `home://.config/i3/config` or `system://etc/foo`. path is original native absolute path. Non-file resources may use other URI schemes and empty path; file-only exporters explicitly reject unsupported kinds.
* `Finding(id: str, module: str, title: str, summary: str, resources: list[Resource], evidence: list[str], confidence: int, dependencies: list[str] = [], warnings: list[str] = [], migration: str = "review")`. confidence is an evidence strength score 0..100, not a probability. No claim about authorship. Helper `finding_id(module, key)` returns module + short stable SHA256(key).
* `Check(id: str, status: str, detail: str, weight: int = 1)`. Status `complete`, `partial`, `blocked`, `unsupported`, `excluded`, or `not_applicable`. Partial receives zero credit, complete full credit. Denominator includes missing/blocked/excluded checks; not_applicable is omitted. Checks describe declared testable use cases, not machine-wide completeness.
* `ModuleResult(findings: list[Finding] = [], checks: list[Check] = [], warnings: list[str] = [])` with default factories.
* `ModuleSpec(id: str, title: str, platforms: tuple[str, ...], checks: tuple[tuple[str, str], ...], api_version: int = 1)`.
* `Snapshot(id: str, created_at: str, platform: str, distro: str, home: str, findings: list[Finding], checks: list[Check], warnings: list[str], schema_version: int = 1, scope: dict = {})` (scope uses a default factory). Scope records root, home, selected scope/modules, includes and exclusions; missing or changed provenance prevents a missing finding being called deleted.
* `coverage(checks)` returns `{percent: float | None, completed: int, total: int, statuses: dict, interpretation: str}`. Core doesn't average finding confidence into coverage.

`driftstamp.context.ScanContext(root: Path, home: Path, platform: str = "linux", distro: str = "unknown", scope: str = "all", includes: tuple[Path,...] = (), excludes: tuple[str,...] = ())`:
* root is actual root/mounted fixture. home is physical path beneath root (or ordinary home for native root).
* `system(relative: str) -> Path` gives root / relative (without leading slash).
* `uri(path: Path) -> str` maps path lexically to home:// or system:// (not resolved targets).
* `allowed(path: Path) -> bool` applies excludes and prevents reads outside root. For native root '/', system paths are permitted. Symlinks that escape a fixture root cannot be read. Home scope restricts system discovery; modules enforce domain through scope checks.
* `read_text(path: Path) -> str` reads at most 1MiB strict UTF-8 (OSError/ValueError for skipped/unreadable/too big/binary). Does not execute or expand arbitrary expressions.
* `resource(path: Path) -> Resource` hashes file content, captures mode and link target; files max 1MiB, raises on errors or unsupported kinds.
* `walk_files(path: Path) -> list[Path]` sorted, no directory symlink traversal, max 2000 entries; failures raise (never claim completion after an unreadable subtree). Missing roots return empty.
* `resolve_reference(value: str, relative_to: Path) -> Path | None` supports literal ~/ and $HOME/ and ${HOME}/ plus absolute paths remapped to root for fixtures; returns None for other variables, backticks, $(...), or shell compound syntax. Resolution does not execute anything.

## Discovery modules

Each class `spec: ModuleSpec`, `scan(ctx: ScanContext) -> ModuleResult`.
`modules.builtin_modules() -> list[module instance]`.
Builtins: i3 (configuration + literal includes/referenced scripts), shell (Bash/Zsh profile candidates and literal source references), scripts (local script dirs + explicit includes), systemd (local user/system unit files/drop-ins + literal ExecStart scripts), cron (local user spool and /etc cron configuration). Package-baseline comparison is a pending capability, not a shipped module.
Check IDs prefixed by module ID, matching spec.checks exactly; skipped scope must report `excluded`.
Engine supplies a status for unreported checks and missing modules, so support gaps remain visible.
Low-battery timer -> service -> script is a useful deterministic test fixture.
Don't use subprocess or scan the real user's environment in tests. Describe conservative limitations in checks and warnings. Prefix spec check IDs consistently (`i3.config`, `i3.references`, etc.). i3 source expressions are never evaluated.

## Registry and engine

`registry.load_modules(allow_external=False) -> (list[modules], list[str])`: discover installed `driftstamp.modules` entry points only when allow_external=True; reject duplicate IDs/API mismatches; failures become warnings.
`registry.module_catalog(allow_external=False) -> list[dict]` provides id/title/platforms/checks/api_version.
`engine.scan(ctx, modules=None, selected=None, name=None, now=None, warnings=None) -> Snapshot`: modules defaults builtins; selected list IDs restricts execution but excluded check statuses remain. Unknown IDs raise ValueError. now is optional fixed timestamp string for tests. name optional ID otherwise timestamp/random unique safe filename. Registry warnings become a partial plugin-loading check.
`engine.compare(before: Snapshot, after: Snapshot) -> dict` with added/removed/changed finding IDs, scope gaps reported separately so absent findings in unscanned modules are not called deleted.
`engine.snapshot_from_dict(data) -> Snapshot`: validates version and reconstructs dataclasses.

## Persistence

`Store(directory: Path)`:
* `save(snapshot) -> None`; named snapshots immutable, collision ValueError. Publication uses a temporary file and hard link, so the state filesystem must support hard links.
* `load(name="latest") -> Snapshot`
* `review(finding_id, decision, note=None) -> dict` validates against latest findings, decisions undecided/keep/skip/investigate. Stable ID metadata persists in `reviews.json`.
* `reviews() -> dict` maps ID to `{decision, note}`.
* `snapshots() -> list[str]`.
default state dir from XDG_STATE_HOME or Windows LOCALAPPDATA, then user fallback; CLI `--state-dir` override avoids host state in tests.

## Migration

`export_bundle(snapshot, reviews, output: Path) -> dict`: selected keep findings only, content copied with hash verification into content-addressed relative paths. Never overwrite existing directory. Manifest retains source evidence and resource metadata. Refuse changed/missing or unsupported selected resources; links preserved as manifest link metadata and don't create live links. No restore execution.
`plan_bundle(bundle: Path, target: str, release=None, check_host=False) -> dict`: read manifest, verify bundled content hashes/path containment, use `driftstamp.targets` entry-point adapters (allow_external opt-in added as kwarg) plus builtins Ubuntu/Arch; commands represented as argv arrays for display only, unknown deps explicitly unresolved. No shell or filesystem application. Plan resources use copy/adapt/merge/review hints; system files default merge/review; machine-specific warnings retained. Ubuntu/Arch cross OS intent is NOT automatically translated to Windows. Windows and RHEL target support declared pending.

## CLI contract

`main(argv=None) -> int` catches expected errors without traceback; return 0 success, 1 runtime failure, 2 argument misuse (argparse), 3 partial scan (unsupported/excluded declared checks affect coverage, only blocked/partial errors trigger code 3). JSON output stdout; errors stderr.
Global flags accepted before or after subcommands: --state-dir PATH, --format text|json, --no-color, --allow-plugins (opt-in loading external trusted Python code).
Commands:
* `scan --root PATH --home PATH --platform linux|windows --distro NAME --scope user|system|all --module ID (repeatable) --include PATH --exclude GLOB --name NAME`. --root defaults native root, --home defaults original invoking user (Linux sudo safe resolution); explicit fixture roots require explicit --home. Do not implement elevated operations.
* `list --scan NAME --module ID --decision STATE --min-confidence 0..100 --needs-review`
* `show ID --scan NAME` full evidence/resources/dependencies/warnings; don't add flags without implementation.
* `review ID --decision STATE [--note TEXT]`; only explicit noninteractive decisions are implemented.
* `diff OLD [NEW]` default latest.
* `coverage --scan NAME` report score AND check details/gaps.
* `modules` catalog implemented modules + notes about planned platform coverage.
* `export --scan NAME --output PATH`
* `plan BUNDLE --target ubuntu|arch --release VERSION --check-host` (target must be free string for plugins, not fixed argparse choices).
* `snapshots`

Tests use temporary roots/state, fixed clocks, fake metadata entry points. No sudo, internet, installed package database, active desktop, or human prompts. Cross-platform core tests avoid assuming chmod semantics. Symlink/permission prerequisites must be capability skipped where unavailable. Windows CI is configured but has not run in this local workspace.
