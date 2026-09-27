"""Explicit, versioned plugin loading through Python entry points."""
from __future__ import annotations

from dataclasses import asdict
from importlib.metadata import entry_points
import re

from .model import ModuleSpec


def validate_module(module):
    spec = getattr(module, "spec", None)
    if not isinstance(spec, ModuleSpec) or type(spec.api_version) is not int or spec.api_version != 1:
        raise ValueError("Unsupported discovery plugin API version")
    if (not isinstance(spec.id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", spec.id)
            or spec.id in {"plugins", "platform"}):
        raise ValueError("Invalid module ID")
    if not isinstance(spec.title, str) or not spec.title or "\x00" in spec.title:
        raise ValueError("Module title must be a nonempty string")
    if (not isinstance(spec.platforms, (tuple, list)) or not spec.platforms
            or any(not isinstance(p, str) or not re.fullmatch(r"\*|[a-z][a-z0-9_-]*", p) for p in spec.platforms)
            or len(set(spec.platforms)) != len(spec.platforms)):
        raise ValueError("Module platforms must be a nonempty sequence of unique platform names")
    if (not isinstance(spec.checks, (tuple, list)) or not spec.checks
            or any(not isinstance(item, (tuple, list)) or len(item) != 2
                   or any(not isinstance(value, str) or not value or "\x00" in value for value in item)
                   for item in spec.checks)):
        raise ValueError("Module checks must be a nonempty sequence of ID and description pairs")
    ids = [item[0] for item in spec.checks]
    if len(ids) != len(set(ids)) or any(not key.startswith(spec.id + ".") or key == spec.id + "." for key in ids):
        raise ValueError("Module check IDs must be unique and start with its module ID")
    if not callable(getattr(module, "scan", None)):
        raise ValueError("Plugin must implement scan(context)")
    return module


def load_modules(allow_external: bool = False):
    from .modules import builtin_modules
    modules = [validate_module(m) for m in builtin_modules()]
    warnings = []
    seen = {m.spec.id for m in modules}
    if allow_external:
        try:
            entries = sorted(entry_points(group="driftstamp.modules"), key=lambda e: e.name)
        except Exception as exc:
            return modules, [f"Cannot enumerate discovery plugins: {exc}"]
        for entry in entries:
            try:
                module = validate_module(entry.load()())
                if module.spec.id in seen:
                    raise ValueError(f"Duplicate module ID: {module.spec.id}")
                modules.append(module)
                seen.add(module.spec.id)
            except Exception as exc:
                warnings.append(f"Plugin {entry.name} was not loaded: {exc}")
    return modules, warnings


def module_catalog(allow_external: bool = False):
    modules, warnings = load_modules(allow_external)
    result = [asdict(m.spec) for m in modules]
    if warnings:
        result.append({"id": "plugin-errors", "title": "Plugin load failures", "warnings": warnings})
    return result
