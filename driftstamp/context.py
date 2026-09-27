"""Bounded file reads for discovery. No command evaluation or shell invocation."""
from __future__ import annotations

import fnmatch
import hashlib
import os
from pathlib import Path
import re
import stat
from dataclasses import dataclass, field

from .model import Resource

MAX_FILE_BYTES = 1024 * 1024
MAX_ENTRIES = 2000


@dataclass
class ScanContext:
    root: Path
    home: Path
    platform: str = "linux"
    distro: str = "unknown"
    scope: str = "all"
    includes: tuple[Path, ...] = ()
    excludes: tuple[str, ...] = ()
    issues: list[dict] = field(default_factory=list, init=False)

    def __post_init__(self):
        self.root = Path(os.path.abspath(self.root))
        self.home = Path(os.path.abspath(self.home))
        if not self.root.is_dir():
            raise ValueError("Scan root must be an existing directory")
        try:
            contained = self.home.is_relative_to(self.root) and self.home.resolve().is_relative_to(self.root.resolve())
        except (OSError, RuntimeError) as exc:
            raise ValueError("Scan home cannot be resolved") from exc
        if not contained:
            raise ValueError("Scan home must be inside the scan root")
        if self.scope not in {"user", "system", "all"}:
            raise ValueError("Scope must be user, system, or all")
        if self.scope != "system" and not self.home.is_dir():
            raise ValueError("Scan home must be an existing directory")

    def system(self, relative: str) -> Path:
        path = self.root / relative.lstrip("/\\")
        if ".." in path.relative_to(self.root).parts:
            raise ValueError("System path cannot escape root")
        return path

    def uri(self, path: Path) -> str:
        path = Path(os.path.abspath(path))
        if path.is_relative_to(self.home):
            return "home://" + path.relative_to(self.home).as_posix()
        if path.is_relative_to(self.root):
            return "system://" + path.relative_to(self.root).as_posix()
        raise ValueError("Path is outside scan root")

    def _excluded(self, path: Path) -> bool:
        while path.is_relative_to(self.root):
            candidates = [str(path), self.uri(path), path.relative_to(self.root).as_posix()]
            if path.is_relative_to(self.home):
                candidates.append("~/" + path.relative_to(self.home).as_posix())
            if any(fnmatch.fnmatchcase(value, pattern) for pattern in self.excludes for value in candidates):
                return True
            if path == self.root:
                break
            path = path.parent
        return False

    def allowed(self, path: Path) -> bool:
        path = Path(os.path.abspath(path))
        try:
            resolved = path.resolve()
            if not path.is_relative_to(self.root) or not resolved.is_relative_to(self.root.resolve()):
                return False
            # Translate through a possibly symlinked root before matching URIs.
            resolved_in_root = self.root / resolved.relative_to(self.root.resolve())
            if self._excluded(path) or self._excluded(resolved_in_root):
                return False
            resolved_home = self.home.resolve()
            if resolved.is_relative_to(resolved_home) and self._excluded(self.home / resolved.relative_to(resolved_home)):
                return False
            return True
        except (OSError, RuntimeError):
            return False

    def _data(self, path: Path) -> bytes:
        if not self.allowed(path):
            raise ValueError(f"Excluded or outside scan root: {path}")
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError(f"Not a regular file: {path}")
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"File exceeds {MAX_FILE_BYTES} byte inspection limit: {path}")
        return data

    def read_text(self, path: Path) -> str:
        data = self._data(Path(path))
        if b"\x00" in data:
            raise ValueError(f"Binary file: {path}")
        return data.decode("utf-8")

    def resource(self, path: Path) -> Resource:
        path = Path(path).absolute()
        if not self.allowed(path):
            raise ValueError(f"Excluded or outside scan root: {path}")
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            target = os.readlink(path)
            return Resource(self.uri(path), str(path), hashlib.sha256(target.encode()).hexdigest(), mode, target)
        data = self._data(path)
        return Resource(self.uri(path), str(path), hashlib.sha256(data).hexdigest(), mode)

    def walk_files(self, path: Path) -> list[Path]:
        path = Path(path)
        if not self.allowed(path):
            raise ValueError(f"Excluded or outside scan root: {path}")
        if not path.exists() and not path.is_symlink():
            return []
        if path.is_symlink() or path.is_file():
            return [path]
        result: list[Path] = []
        visited = 0

        def onerror(error):
            raise error

        for parent, dirs, files in os.walk(path, followlinks=False, onerror=onerror):
            dirs.sort()
            files.sort()
            keep = []
            for name in dirs:
                child = Path(parent) / name
                visited += 1
                if not self.allowed(child):
                    self.issues.append({"status": "excluded", "path": str(child)})
                elif child.is_symlink():
                    result.append(child)
                    self.issues.append({"status": "partial", "path": str(child), "reason": "Directory symlink not traversed"})
                else:
                    keep.append(name)
            dirs[:] = keep
            for name in files:
                child = Path(parent) / name
                visited += 1
                if self.allowed(child):
                    result.append(child)
                else:
                    self.issues.append({"status": "excluded", "path": str(child)})
            if visited > MAX_ENTRIES:
                raise ValueError(f"Directory exceeds {MAX_ENTRIES} entry inspection limit: {path}")
        return sorted(result)

    def resolve_reference(self, value: str, relative_to: Path) -> Path | None:
        value = value.strip().strip('"\'')
        if any(token in value for token in ("$(", "`", ";", "|", "&", "\n", "\r", "*", "?", "[")):
            return None
        for prefix in ("~/", "$HOME/", "${HOME}/"):
            if value.startswith(prefix):
                value = str(self.home / value[len(prefix):])
                break
        if "$" in value or "%" in value or value.startswith("~"):
            return None
        path = Path(value)
        if not value:
            return None
        if self.platform == "linux" and value.startswith("/") and not path.is_absolute():
            path = self.root / value.lstrip("/")
        elif path.is_absolute():
            if not path.is_relative_to(self.root):
                path = self.root / value.lstrip("/\\")
        else:
            path = relative_to / path
        path = Path(os.path.abspath(path))
        return path if self.allowed(path) else None
