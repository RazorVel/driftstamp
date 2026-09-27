#!/usr/bin/env python3
"""Install a built Driftstamp wheel into a private POSIX user environment."""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import venv
from zipfile import BadZipFile, ZipFile

MARKER = {"application": "driftstamp", "installer_schema": 1}


def wheel_manual(wheel: Path) -> bytes:
    """Check the selected distribution before touching an installation."""
    with ZipFile(wheel) as archive:
        metadata_paths = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata_paths) != 1:
            raise ValueError("Expected one Driftstamp distribution in the wheel")
        metadata = BytesParser().parsebytes(archive.read(metadata_paths[0]))
        if metadata.get("Name") != "driftstamp":
            raise ValueError("The selected wheel is not Driftstamp")
        manuals = [name for name in archive.namelist()
                   if name.endswith(".data/data/share/man/man1/driftstamp.1")]
        if len(manuals) != 1:
            raise ValueError("The Driftstamp wheel must contain its manual")
        return archive.read(manuals[0])


def installation_paths(prefix: Path) -> tuple[Path, Path, Path]:
    return (prefix / "share/driftstamp", prefix / "bin/driftstamp",
            prefix / "share/man/man1/driftstamp.1")


def preflight(prefix: Path) -> tuple[Path, Path, Path]:
    location, command, manual = installation_paths(prefix)
    recognized = False
    if location.exists() or location.is_symlink():
        marker = location / "install.json"
        if location.is_symlink() or marker.is_symlink() or not marker.is_file() or json.loads(marker.read_text()) != MARKER:
            raise ValueError(f"Refusing to reuse an unrecognized installation: {location}")
        recognized = True
    environments = location / "environments"
    if environments.is_symlink() or (environments.exists() and not environments.is_dir()):
        raise ValueError("The private environments location must be a directory, not a symlink")
    if command.exists() or command.is_symlink():
        owned = False
        if recognized and command.is_symlink():
            target = command.readlink()
            if target.is_relative_to(environments):
                parts = target.relative_to(environments).parts
                owned = (len(parts) == 3 and re.fullmatch(r"env-[A-Za-z0-9_-]+", parts[0]) is not None
                         and parts[1:] == ("bin", "driftstamp")
                         and not target.parent.is_symlink() and not target.parent.parent.is_symlink())
        if not owned:
            raise ValueError(f"Refusing to replace an unrelated command: {command}")
    if manual.is_symlink() or (manual.exists() and not manual.is_file()):
        raise ValueError(f"Refusing to replace a non-file manual: {manual}")
    return location, command, manual


def install(wheel: Path, prefix: Path) -> None:
    if os.name != "posix":
        raise ValueError("This user installer supports POSIX; use a Python virtual environment on Windows")
    wheel = wheel.resolve(strict=True)
    if wheel.suffix != ".whl":
        raise ValueError("Select a built .whl file")
    manual_data = wheel_manual(wheel)
    prefix = Path(os.path.abspath(prefix.expanduser()))
    location, command, manual = preflight(prefix)
    location.mkdir(parents=True, exist_ok=True)
    (location / "install.json").write_text(json.dumps(MARKER) + "\n", encoding="utf-8")
    environments = location / "environments"
    environments.mkdir(exist_ok=True)
    # A venv's scripts contain absolute interpreter paths; keep its final path.
    environment = Path(tempfile.mkdtemp(prefix="env-", dir=environments))
    published = False
    try:
        venv.EnvBuilder(with_pip=True).create(environment)
        subprocess.run([str(environment / "bin/python"), "-m", "pip", "install", "--no-index",
                        "--no-deps", "--no-cache-dir", "--disable-pip-version-check", str(wheel)], check=True)
        executable = environment / "bin/driftstamp"
        subprocess.run([str(executable), "--version"], cwd=prefix, check=True)
        # An unrelated command may have appeared while the environment was built.
        preflight(prefix)
        manual.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".driftstamp-", dir=manual.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(manual_data)
            os.chmod(name, 0o644)
            os.replace(name, manual)
        finally:
            Path(name).unlink(missing_ok=True)
        command.parent.mkdir(parents=True, exist_ok=True)
        # A private temporary directory avoids racing a fixed symlink name.
        with tempfile.TemporaryDirectory(prefix=".driftstamp-", dir=command.parent) as temporary:
            link = Path(temporary) / "driftstamp"
            link.symlink_to(executable)
            link.replace(command)
            published = True
    finally:
        if not published:
            shutil.rmtree(environment, ignore_errors=True)
    print(f"Installed command: {command}\nInstalled manual: {manual}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path, help="trusted Driftstamp wheel built from this project")
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local",
                        help="user prefix (default: ~/.local)")
    args = parser.parse_args(argv)
    try:
        install(args.wheel, args.prefix)
    except (OSError, ValueError, BadZipFile, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Installation failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
