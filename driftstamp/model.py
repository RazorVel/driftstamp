"""Versioned, platform-neutral records shared by the core and plugins."""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import re

STATUSES = frozenset({"complete", "partial", "blocked", "unsupported", "excluded", "not_applicable"})
DECISIONS = frozenset({"undecided", "keep", "skip", "investigate"})


@dataclass
class Resource:
    uri: str
    path: str
    sha256: str | None = None
    mode: int | None = None
    symlink: str | None = None
    kind: str = "file"


@dataclass
class Finding:
    id: str
    module: str
    title: str
    summary: str
    resources: list[Resource]
    evidence: list[str]
    confidence: int
    dependencies: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    migration: str = "review"

    def __post_init__(self):
        if type(self.confidence) is not int or not 0 <= self.confidence <= 100:
            raise ValueError("Finding confidence must be an integer between 0 and 100")


@dataclass
class Check:
    id: str
    status: str
    detail: str
    weight: int = 1

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f"Unknown check status: {self.status}")
        if type(self.weight) is not int or self.weight < 1:
            raise ValueError("Check weight must be a positive integer")


@dataclass
class ModuleResult:
    findings: list[Finding] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ModuleSpec:
    id: str
    title: str
    platforms: tuple[str, ...]
    checks: tuple[tuple[str, str], ...]
    api_version: int = 1


@dataclass
class Snapshot:
    id: str
    created_at: str
    platform: str
    distro: str
    home: str
    findings: list[Finding]
    checks: list[Check]
    warnings: list[str]
    schema_version: int = 1
    scope: dict = field(default_factory=dict)


def finding_id(module: str, key: str) -> str:
    return f"{module}-{sha256(key.encode('utf-8')).hexdigest()[:12]}"


def _text(value, label, *, empty=False):
    if not isinstance(value, str) or (not empty and not value) or "\x00" in value:
        raise ValueError(f"{label} must be {'a' if empty else 'a nonempty'} string without NUL characters")


def _strings(value, label):
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list of strings")
    for item in value:
        _text(item, label, empty=True)


def validate_resource(resource: Resource) -> None:
    """Validate record types without reading their referenced locations."""
    if not isinstance(resource, Resource):
        raise ValueError("Invalid resource record")
    _text(resource.kind, "Resource kind")
    _text(resource.uri, "Resource URI")
    _text(resource.path, "Resource path", empty=resource.kind != "file")
    if not re.fullmatch(r"[a-z][a-z0-9+.-]*://.+", resource.uri):
        raise ValueError("Resource URI must have a scheme and nonempty locator")
    if resource.kind == "file" and resource.uri.startswith(("home://", "system://")):
        relative = resource.uri.split("://", 1)[1]
        if any(part in {"", ".", ".."} for part in relative.split("/")) or "\\" in relative or ":" in relative:
            raise ValueError("File resource URI must be a canonical relative path")
    if resource.sha256 is not None and (not isinstance(resource.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", resource.sha256)):
        raise ValueError("Resource checksum must be a SHA-256 hex digest or null")
    if resource.mode is not None and (type(resource.mode) is not int or not 0 <= resource.mode <= 0o7777):
        raise ValueError("Resource mode must be permission bits or null")
    if resource.symlink is not None:
        _text(resource.symlink, "Link target")
        if resource.kind != "file":
            raise ValueError("Only file resources can have link targets")


def validate_finding(finding: Finding) -> None:
    if not isinstance(finding, Finding):
        raise ValueError("Invalid finding record")
    for label in ("id", "module", "title", "summary", "migration"):
        _text(getattr(finding, label), f"Finding {label}", empty=label == "summary")
    if type(finding.confidence) is not int or not 0 <= finding.confidence <= 100:
        raise ValueError("Finding confidence must be an integer between 0 and 100")
    if not isinstance(finding.resources, list):
        raise ValueError("Finding resources must be a list")
    for resource in finding.resources:
        validate_resource(resource)
    for label in ("evidence", "dependencies", "warnings"):
        _strings(getattr(finding, label), f"Finding {label}")


def validate_check(check: Check) -> None:
    if not isinstance(check, Check):
        raise ValueError("Invalid check record")
    _text(check.id, "Check ID")
    _text(check.detail, "Check detail", empty=True)
    if not isinstance(check.status, str) or check.status not in STATUSES:
        raise ValueError("Invalid check status")
    if type(check.weight) is not int or check.weight < 1:
        raise ValueError("Check weight must be a positive integer")


def validate_snapshot(snapshot: Snapshot) -> None:
    if not isinstance(snapshot, Snapshot) or type(snapshot.schema_version) is not int or snapshot.schema_version != 1:
        raise ValueError("Unsupported snapshot schema version")
    for label in ("id", "created_at", "platform", "distro", "home"):
        _text(getattr(snapshot, label), f"Snapshot {label}")
    if not isinstance(snapshot.findings, list) or not isinstance(snapshot.checks, list):
        raise ValueError("Snapshot findings and checks must be lists")
    for finding in snapshot.findings:
        validate_finding(finding)
    for check in snapshot.checks:
        validate_check(check)
    if len({f.id for f in snapshot.findings}) != len(snapshot.findings) or len({c.id for c in snapshot.checks}) != len(snapshot.checks):
        raise ValueError("Duplicate record IDs in snapshot")
    _strings(snapshot.warnings, "Snapshot warnings")
    if not isinstance(snapshot.scope, dict):
        raise ValueError("Snapshot scope must be an object")
    if snapshot.scope:
        for key in ("root", "home", "scope"):
            _text(snapshot.scope.get(key), f"Snapshot scope {key}")
        if snapshot.scope["scope"] not in {"user", "system", "all"}:
            raise ValueError("Invalid saved scan scope")
        for key in ("includes", "excludes", "modules"):
            _strings(snapshot.scope.get(key), f"Snapshot scope {key}")


def coverage(checks: list[Check]) -> dict:
    """Conservative completion of a declared checklist, never recall or accuracy."""
    if len({c.id for c in checks}) != len(checks):
        raise ValueError("Duplicate coverage check IDs")
    applicable = [c for c in checks if c.status != "not_applicable"]
    total = sum(c.weight for c in applicable)
    complete = sum(c.weight for c in applicable if c.status == "complete")
    return {
        "percent": round(100 * complete / total, 1) if total else None,
        "completed": complete,
        "total": total,
        "statuses": {s: sum(c.status == s for c in checks) for s in sorted(STATUSES)},
        "interpretation": "Declared use-case inspection completion; not the percentage of all tweaks found. Partial checks receive no completion credit. Finding confidence is an uncalibrated evidence score, not probability.",
    }
