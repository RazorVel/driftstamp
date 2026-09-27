"""Local immutable snapshots and separate, persistent user review decisions."""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile

from .engine import safe_name, snapshot_from_dict
from .model import DECISIONS, validate_snapshot


def default_state_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "driftstamp"
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))) / "driftstamp"


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".driftstamp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Store:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def save(self, snapshot):
        validate_snapshot(snapshot)
        name = safe_name(snapshot.id)
        folder = self.directory / "snapshots"
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = folder / (name + ".json")
        fd, temporary_name = tempfile.mkstemp(prefix=".snapshot-", dir=folder)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(asdict(snapshot), stream, indent=2, sort_keys=True, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # Publish a complete file without overwriting an immutable name.
                os.link(temporary, target)
            except FileExistsError as exc:
                raise ValueError(f"Snapshot already exists: {name}") from exc
        finally:
            temporary.unlink(missing_ok=True)
        atomic_json(self.directory / "latest.json", {"id": name})

    def load(self, name="latest"):
        try:
            if name == "latest":
                pointer = json.loads((self.directory / "latest.json").read_text(encoding="utf-8"))
                if not isinstance(pointer, dict) or not isinstance(pointer.get("id"), str):
                    raise ValueError("Invalid latest snapshot reference")
                name = pointer["id"]
            safe_name(name)
            data = json.loads((self.directory / "snapshots" / (name + ".json")).read_text(encoding="utf-8"))
            snapshot = snapshot_from_dict(data)
            if snapshot.id != name:
                raise ValueError("Snapshot ID does not match its saved name")
            return snapshot
        except FileNotFoundError as exc:
            raise ValueError(f"Snapshot not found: {name}; run scan first") from exc
        except (json.JSONDecodeError, KeyError) as exc:
            raise ValueError("Invalid snapshot state") from exc

    def reviews(self):
        path = self.directory / "reviews.json"
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Invalid review state")
        if any(not isinstance(key, str) or not isinstance(value, dict)
               or not isinstance(value.get("decision"), str) or value.get("decision") not in DECISIONS
               or not isinstance(value.get("note"), str) for key, value in data.items()):
            raise ValueError("Invalid review record")
        return data

    def review(self, finding_id, decision, note=None):
        if not isinstance(decision, str) or decision not in DECISIONS:
            raise ValueError("Unknown review decision")
        if note is not None and not isinstance(note, str):
            raise ValueError("Review note must be text")
        if finding_id not in {f.id for f in self.load().findings}:
            raise ValueError(f"Finding not present in latest scan: {finding_id}")
        data = self.reviews()
        old = data.get(finding_id, {})
        data[finding_id] = {"decision": decision, "note": old.get("note", "") if note is None else note}
        atomic_json(self.directory / "reviews.json", data)
        return data[finding_id]

    def snapshots(self):
        return sorted(path.stem for path in (self.directory / "snapshots").glob("*.json"))
