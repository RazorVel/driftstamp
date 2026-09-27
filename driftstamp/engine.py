"""Collect snapshots and report uncertainty without modifying source settings."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import re
import uuid

from .model import (Check, Finding, ModuleResult, Resource, Snapshot,
                    validate_check, validate_finding, validate_snapshot)
from .registry import load_modules, validate_module

PENDING = {
    "linux": (
        ("package-config", "package-config.baselines", "Package ownership and exact-version configuration baselines are not implemented."),
        ("app-settings", "app-settings.custom", "Arbitrary application preferences and generated configuration are not inspected."),
        ("permissions", "permissions.acl", "ACLs, capabilities, and ownership changes are not compared with a baseline."),
    ),
    "windows": (
        ("powershell", "powershell.profiles", "PowerShell profile discovery is planned, not implemented."),
        ("cmd", "cmd.environment", "CMD AutoRun and persistent environment discovery are planned, not implemented."),
        ("windows-services", "windows-services.tasks", "Windows services and scheduled tasks are planned, not implemented."),
    ),
}


def safe_name(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", name) or name == "latest":
        raise ValueError("Snapshot name must be 1-80 letters, numbers, '.', '_' or '-'; 'latest' is reserved")
    return name


def scan(ctx, modules=None, selected=None, name=None, now=None, warnings=None):
    initial_warnings = list(warnings or [])
    loading_failed = bool(warnings)
    if modules is None:
        modules, errors = load_modules()
        initial_warnings.extend(errors)
        loading_failed = loading_failed or bool(errors)
    modules = [validate_module(m) for m in modules]
    module_ids = [m.spec.id for m in modules]
    if len(set(module_ids)) != len(module_ids):
        raise ValueError("Duplicate module IDs")
    if selected and set(selected) - set(module_ids):
        raise ValueError("Unknown modules: " + ", ".join(sorted(set(selected) - set(module_ids))))
    stamp = now or datetime.now(timezone.utc).isoformat()
    name = safe_name(name if name is not None else datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8])
    findings, checks = [], []
    seen_findings = set()
    for module in modules:
        spec = module.spec
        if ctx.platform not in spec.platforms and "*" not in spec.platforms:
            checks.extend(Check(key, "not_applicable", f"{description}: does not apply on {ctx.platform}") for key, description in spec.checks)
            continue
        if selected and spec.id not in selected:
            checks.extend(Check(key, "excluded", f"{description}: module not selected") for key, description in spec.checks)
            continue
        issue_start = len(ctx.issues)
        try:
            result = module.scan(ctx)
            if not isinstance(result, ModuleResult):
                raise ValueError("Plugin returned an invalid result")
            if (not isinstance(result.findings, list) or not isinstance(result.checks, list)
                    or not isinstance(result.warnings, list)
                    or any(not isinstance(warning, str) or "\x00" in warning for warning in result.warnings)
                    or any(not isinstance(check, Check) for check in result.checks)
                    or any(not isinstance(finding, Finding) for finding in result.findings)):
                raise ValueError("Plugin returned invalid result fields")
            for check in result.checks:
                validate_check(check)
            expected = {key for key, _ in spec.checks}
            provided = {check.id for check in result.checks}
            if len(provided) != len(result.checks) or provided - expected:
                raise ValueError("Plugin returned undeclared or duplicate checks")
            local_ids = set()
            for finding in result.findings:
                validate_finding(finding)
                if finding.module != spec.id or finding.id in seen_findings or finding.id in local_ids:
                    raise ValueError("Plugin returned inconsistent or duplicate finding IDs")
                local_ids.add(finding.id)
            gaps = ctx.issues[issue_start:]
            result_checks = list(result.checks)
            result_warnings = list(result.warnings)
            if gaps:
                gap_status = "excluded" if all(g["status"] == "excluded" for g in gaps) else "partial"
                result_checks = [replace(c, status=gap_status, detail=c.detail + f"; {len(gaps)} traversal gaps") if c.status == "complete" else c for c in result_checks]
                result_warnings.extend(f"{g['status']}: {g['path']}" for g in gaps)
            checks.extend(result_checks)
            checks.extend(Check(key, "partial", description + ": module did not report completion") for key, description in spec.checks if key not in provided)
            findings.extend(result.findings)
            seen_findings.update(local_ids)
            initial_warnings.extend(result_warnings)
        except Exception as exc:
            checks.extend(Check(key, "blocked", f"{description}: {type(exc).__name__}: {exc}") for key, description in spec.checks)
            initial_warnings.append(f"Module {spec.id} failed: {exc}")
    for _, check_id, detail in PENDING.get(ctx.platform, ()):
        matching = next((i for i, check in enumerate(checks) if check.id == check_id), None)
        if matching is None:
            checks.append(Check(check_id, "unsupported", detail))
        elif checks[matching].status == "not_applicable":
            checks[matching] = Check(check_id, "unsupported", detail)
    if ctx.platform not in PENDING and not any(c.status != "not_applicable" for c in checks):
        checks.append(Check("platform.discovery", "unsupported", f"No declared discovery support for {ctx.platform}"))
    if loading_failed:
        checks.append(Check("plugins.load", "partial", "One or more installed plugins could not be loaded"))
    scope = {"root": str(ctx.root), "home": str(ctx.home), "scope": ctx.scope,
             "includes": sorted({str(p) for p in ctx.includes}), "excludes": sorted(set(ctx.excludes)),
             "modules": sorted(set(selected or module_ids))}
    return Snapshot(name, stamp, ctx.platform, ctx.distro, str(ctx.home),
                    sorted(findings, key=lambda f: (f.module, f.id)), sorted(checks, key=lambda c: c.id), sorted(set(initial_warnings)), scope=scope)


def snapshot_from_dict(data):
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data.get("schema_version") != 1:
        raise ValueError("Unsupported snapshot schema version")
    try:
        if not isinstance(data["findings"], list) or not isinstance(data["checks"], list):
            raise ValueError("Snapshot findings and checks must be lists")
        for finding in data["findings"]:
            if not isinstance(finding, dict) or not isinstance(finding["resources"], list):
                raise ValueError("Invalid finding structure")
        findings = [Finding(**{**f, "resources": [Resource(**r) for r in f["resources"]]}) for f in data["findings"]]
        checks = [Check(**c) for c in data["checks"]]
        result = Snapshot(**{**data, "findings": findings, "checks": checks})
        validate_snapshot(result)
        return result
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid snapshot structure: {exc}") from exc


def _fingerprint(finding):
    return (finding.summary, tuple(sorted(finding.dependencies)),
            sorted((r.uri, r.sha256 or "", r.mode or 0, r.symlink or "", r.kind) for r in finding.resources))


def compare(before: Snapshot, after: Snapshot):
    old = {f.id: f for f in before.findings}
    new = {f.id: f for f in after.findings}
    removed, unobserved = [], []
    comparable_scope = (bool(before.scope) and before.scope == after.scope
                        and before.platform == after.platform and before.distro == after.distro)
    for key in sorted(old.keys() - new.keys()):
        module_checks = [c for c in after.checks if c.id.startswith(old[key].module + ".")]
        previous_checks = {c.id for c in before.checks if c.id.startswith(old[key].module + ".")}
        catalog_preserved = bool(previous_checks) and previous_checks <= {c.id for c in module_checks}
        destination = removed if comparable_scope and catalog_preserved and module_checks and all(c.status in {"complete", "not_applicable"} for c in module_checks) and any(c.status == "complete" for c in module_checks) else unobserved
        destination.append(key)
    return {"before": before.id, "after": after.id,
            "added": sorted(new.keys() - old.keys()), "removed": removed,
            "changed": sorted(key for key in old.keys() & new.keys() if _fingerprint(old[key]) != _fingerprint(new[key])),
            "not_observed": unobserved,
            "warnings": ["Missing findings with changed/unknown scan scope or incomplete modules are not evidence of deletion."] if unobserved else []}
