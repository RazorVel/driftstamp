"""Export reviewed evidence and produce plans. Never apply machine changes."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from importlib.metadata import entry_points
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import tempfile

from .context import MAX_FILE_BYTES
from .store import atomic_json
from .model import Resource, validate_resource


@dataclass(frozen=True)
class TargetSpec:
    id: str
    title: str
    platforms: tuple[str, ...]
    api_version: int = 1


class LinuxTarget:
    """Small, explicitly curated command-to-package suggestions, not a solver."""
    def __init__(self, target):
        self.spec = TargetSpec(target, target.title(), ("linux",))

    def resolve(self, command, release=None):
        ubuntu = {"xrandr": "x11-xserver-utils", "notify-send": "libnotify-bin", "python3": "python3", "bash": "bash", "zsh": "zsh", "i3": "i3-wm", "systemctl": "systemd"}
        arch = {"xrandr": "xorg-xrandr", "notify-send": "libnotify", "python3": "python", "bash": "bash", "zsh": "zsh", "i3": "i3-wm", "systemctl": "systemd"}
        return (ubuntu if self.spec.id == "ubuntu" else arch).get(command)

    def install_argv(self, packages):
        if not packages:
            return []
        prefix = ["sudo", "apt-get", "install", "--"] if self.spec.id == "ubuntu" else ["sudo", "pacman", "-S", "--needed", "--"]
        return prefix + sorted(packages)

    def resource_action(self, resource, finding):
        if resource["symlink"] is not None:
            return "review", "Recreate this link after reviewing its destination-specific target."
        if resource["uri"].startswith("system://"):
            return "merge", "Review against destination system configuration and defaults."
        if finding.get("warnings"):
            return "adapt", "Review discovery warnings and machine-specific values."
        return "copy", "Candidate for copying after destination checks."


def load_target(name, allow_external=False):
    targets = {name: LinuxTarget(name) for name in ("ubuntu", "arch")}
    if allow_external:
        for entry in sorted(entry_points(group="driftstamp.targets"), key=lambda e: e.name):
            try:
                target = entry.load()()
                spec = target.spec
                if not isinstance(spec, TargetSpec) or type(spec.api_version) is not int or spec.api_version != 1:
                    raise ValueError("Unsupported target API")
                if not isinstance(spec.id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", spec.id):
                    raise ValueError("Invalid target ID")
                if (not isinstance(spec.title, str) or not spec.title or "\x00" in spec.title
                        or not isinstance(spec.platforms, (tuple, list)) or not spec.platforms
                        or any(not isinstance(p, str) or not p or "\x00" in p for p in spec.platforms)):
                    raise ValueError("Invalid target metadata")
                if spec.id in targets:
                    raise ValueError(f"Duplicate target ID: {spec.id}")
                if any(not callable(getattr(target, method, None)) for method in ("resolve", "install_argv", "resource_action")):
                    raise ValueError("Incomplete target adapter")
                targets[spec.id] = target
            except Exception as exc:
                raise ValueError(f"Target plugin {entry.name} could not be loaded: {exc}") from exc
    if name not in targets:
        raise ValueError(f"Unsupported target: {name}. Available: {', '.join(sorted(targets))}. Red Hat and Windows adapters are planned.")
    return targets[name]


def _read_regular(path):
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f"Expected a regular file: {path}")
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"File exceeds export limit: {path}")
    return data


def export_bundle(snapshot, reviews, output: Path):
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError(f"Output already exists: {output}")
    chosen = [f for f in snapshot.findings if reviews.get(f.id, {}).get("decision") == "keep"]
    if not chosen:
        raise ValueError("No findings are marked keep; review findings before export")
    records, blobs, warnings = {}, {}, []
    for finding in chosen:
        warnings.extend(finding.warnings)
        for resource in finding.resources:
            if resource.kind != "file":
                raise ValueError(f"No exporter for resource kind: {resource.kind}")
            _uri_parts(resource.uri)
            path = Path(resource.path)
            if resource.symlink is not None:
                if not path.is_symlink() or os.readlink(path) != resource.symlink:
                    raise ValueError(f"Source link changed since scan: {resource.uri}")
                data = resource.symlink.encode()
                blob = None
                warnings.append(f"Link requires destination review: {resource.uri}")
            else:
                data = _read_regular(path)
                blob = "blobs/" + hashlib.sha256(data).hexdigest()
            digest = hashlib.sha256(data).hexdigest()
            if digest != resource.sha256 or (resource.mode is not None and stat.S_IMODE(path.lstat().st_mode) != resource.mode):
                raise ValueError(f"Source changed since scan; rescan before exporting: {resource.uri}")
            record = {"uri": resource.uri, "sha256": digest, "mode": resource.mode, "symlink": resource.symlink, "kind": resource.kind, "blob": blob}
            if resource.uri in records and records[resource.uri] != record:
                raise ValueError(f"Inconsistent selected resource: {resource.uri}")
            records[resource.uri] = record
            if blob is not None:
                blobs[blob] = data
    manifest = {"schema_version": 1, "source": {"scan": snapshot.id, "platform": snapshot.platform, "distro": snapshot.distro},
                "findings": [{**asdict(f), "review": reviews[f.id]} for f in chosen],
                "files": [records[key] for key in sorted(records)], "warnings": sorted(set(warnings))}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".driftstamp-export-", dir=output.parent))
    try:
        for relative, data in blobs.items():
            destination = temporary / relative
            destination.parent.mkdir(exist_ok=True, mode=0o700)
            destination.write_bytes(data)
            if os.name != "nt":
                destination.chmod(0o600)
        atomic_json(temporary / "manifest.json", manifest)
        if output.exists() or output.is_symlink():
            raise ValueError(f"Output already exists: {output}")
        temporary.rename(output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return manifest


def _uri_parts(uri):
    if not isinstance(uri, str) or "://" not in uri or "\x00" in uri:
        raise ValueError("Invalid resource URI")
    scheme, relative = uri.split("://", 1)
    parts = relative.split("/")
    if (scheme not in {"home", "system"} or any(p in {"", ".", ".."} for p in parts)
            or "\\" in relative or ":" in relative):
        raise ValueError(f"Unsupported or unsafe resource URI: {uri}")
    return scheme, tuple(parts)


def _string_list(value, label, *, nonempty=False):
    if (not isinstance(value, list)
            or any(not isinstance(item, str) or "\x00" in item or (nonempty and not item) for item in value)):
        raise ValueError(f"Invalid bundled {label}")


def _bundle_resource(resource):
    if not isinstance(resource, dict):
        raise ValueError("Invalid bundled file record")
    _uri_parts(resource["uri"])
    # The local source path is informational in a bundle, never a destination.
    validate_resource(Resource(resource["uri"], "bundled", resource["sha256"],
                               resource["mode"], resource["symlink"], resource["kind"]))
    if resource["kind"] != "file":
        raise ValueError("Unsupported bundled resource")
    digest = resource["sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid bundled checksum")


def _load_bundle(bundle):
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != 1):
        raise ValueError("Unsupported bundle schema")
    if (not isinstance(manifest.get("source"), dict)
            or any(not isinstance(manifest["source"].get(key), str) or not manifest["source"][key]
                   or "\x00" in manifest["source"][key] for key in ("scan", "platform", "distro"))
            or not isinstance(manifest.get("files"), list)
            or not isinstance(manifest.get("findings"), list)):
        raise ValueError("Invalid bundle structure")
    _string_list(manifest.get("warnings"), "warnings")
    records = {}
    for resource in manifest["files"]:
        _bundle_resource(resource)
        if resource["uri"] in records:
            raise ValueError("Duplicate bundled resource")
        digest = resource["sha256"]
        if resource["symlink"] is not None:
            if resource["blob"] is not None or hashlib.sha256(resource["symlink"].encode()).hexdigest() != digest:
                raise ValueError("Invalid bundled link")
        else:
            if resource["blob"] != "blobs/" + digest:
                raise ValueError("Invalid bundled content path")
            path = bundle / resource["blob"]
            if not path.resolve().is_relative_to(bundle.resolve()):
                raise ValueError("Bundle content escapes its directory")
            if hashlib.sha256(_read_regular(path)).hexdigest() != digest:
                raise ValueError("Bundle content checksum mismatch")
        records[resource["uri"]] = resource
    referenced = set()
    for finding in manifest["findings"]:
        if not isinstance(finding, dict) or not isinstance(finding.get("resources"), list):
            raise ValueError("Invalid bundled finding record")
        _string_list(finding.get("dependencies"), "dependencies", nonempty=True)
        _string_list(finding.get("warnings"), "finding warnings")
        for resource in finding["resources"]:
            _bundle_resource(resource)
            record = records.get(resource["uri"])
            if record is None or any(record[k] != resource[k] for k in ("sha256", "mode", "symlink", "kind")):
                raise ValueError("Bundle finding and file metadata disagree")
            referenced.add(resource["uri"])
    if referenced != set(records):
        raise ValueError("Bundled file has no associated finding")
    return manifest, records


def _host_action(destination, resource, action, reason):
    # Inspect components from the root down; do not read through redirected parents.
    for parent in reversed(destination.parents):
        if parent.is_symlink():
            return "review", "Destination parent is a symbolic link; review its target."
        if parent.exists() and not parent.is_dir():
            return "review", "Destination parent exists but is not a directory."
    if destination.is_symlink():
        return "review", "Destination is a symbolic link; review its target."
    if not destination.exists():
        return action, reason
    if resource["symlink"] is not None:
        return "review", "Destination exists, but the source is a symbolic link; review the type conflict."
    if not destination.is_file():
        return "review", "Destination exists but is not a regular file."
    try:
        matches = hashlib.sha256(_read_regular(destination)).hexdigest() == resource["sha256"]
        if matches:
            if resource["mode"] is not None and stat.S_IMODE(destination.stat().st_mode) != resource["mode"]:
                return "review", "Content matches, but destination permissions differ."
            return "omit", "Destination content and recorded permissions already match."
    except (OSError, ValueError) as exc:
        return "review", f"Destination could not be checked: {exc}"
    return "merge", "Destination exists with different content; review a merge."


def _host_distro():
    if platform.system().lower() != "linux":
        return platform.system().lower()
    try:
        return platform.freedesktop_os_release().get("ID", "unknown")
    except OSError:
        return "unknown"


def _target_call(adapter, method, *args):
    try:
        return getattr(adapter, method)(*args)
    except Exception as exc:
        raise ValueError(f"Target adapter {adapter.spec.id} failed in {method}: {exc}") from exc


def plan_bundle(bundle: Path, target: str, release=None, check_host=False, allow_external=False):
    adapter = load_target(target, allow_external)
    try:
        manifest, records = _load_bundle(Path(bundle))
    except (KeyError, TypeError, RuntimeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid bundle manifest: {exc}") from exc
    if manifest["source"]["platform"] not in adapter.spec.platforms:
        raise ValueError("This target adapter cannot migrate the source platform; cross-OS translation requires explicit support")
    if check_host and _host_distro() != target:
        raise ValueError(f"Host does not match target {target}; use a general plan without --check-host")
    dependencies = sorted({d for f in manifest["findings"] for d in f["dependencies"]})
    packages, unresolved = set(), []
    for dependency in dependencies:
        package = _target_call(adapter, "resolve", dependency, release)
        if package is None:
            unresolved.append(dependency)
        elif not isinstance(package, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+._:-]*", package):
            raise ValueError("Target adapter returned an invalid package name")
        else:
            packages.add(package)
    resources = []
    for uri, resource in sorted(records.items()):
        associated = [f for f in manifest["findings"] if any(r["uri"] == uri for r in f["resources"])]
        finding = dict(associated[0]) if associated else {"warnings": []}
        finding["warnings"] = [w for f in associated for w in f.get("warnings", [])]
        decision = _target_call(adapter, "resource_action", resource, finding)
        if (not isinstance(decision, (tuple, list)) or len(decision) != 2
                or not isinstance(decision[0], str) or decision[0] not in {"copy", "adapt", "merge", "review", "omit"}
                or not isinstance(decision[1], str) or not decision[1] or "\x00" in decision[1]):
            raise ValueError("Invalid target resource action")
        action, reason = decision
        if check_host:
            scheme, parts = _uri_parts(uri)
            destination = (Path.home() if scheme == "home" else Path(Path.cwd().anchor)).joinpath(*parts)
            action, reason = _host_action(destination, resource, action, reason)
        resources.append({"uri": uri, "action": action, "reason": reason})
    warnings = list(manifest.get("warnings", []))
    warnings.append("Package mappings are curated suggestions; repository availability and version compatibility have not been verified.")
    warnings.append("Service activation, environment, hardware identifiers, and indirect script dependencies require review. No commands have been executed.")
    if release:
        warnings.append(f"Release {release} recorded for review; bundled package mappings are not release-specific.")
    install_argv = _target_call(adapter, "install_argv", sorted(packages))
    if (not isinstance(install_argv, list)
            or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in install_argv)):
        raise ValueError("Target adapter returned an invalid install argument list")
    return {"target": target, "release": release, "source": manifest["source"], "packages": sorted(packages),
            "install_argv": install_argv, "resources": resources,
            "unresolved_dependencies": unresolved, "warnings": sorted(set(warnings)), "host_checked": bool(check_host)}
