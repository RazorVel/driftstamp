#!/usr/bin/env python3
"""Create and inspect a synthetic machine without reading live configuration.

Run from the project checkout:
    python3 examples/demo.py --output /tmp/driftstamp-demo

The output directory must not exist. No fixture command is ever executed.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import shlex
import sys

# Permit direct execution from an uninstalled source checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from driftstamp.context import ScanContext
from driftstamp.engine import scan
from driftstamp.model import coverage
from driftstamp.store import Store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="new directory for synthetic files, snapshot state and report")
    args = parser.parse_args(argv)
    output = args.output.expanduser().absolute()
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error(f"Output directory already exists: {output}")

    root = output / "root"
    home = root / "home/demo"
    state = output / "state"
    files = {
        "etc/os-release": 'ID=ubuntu\nVERSION_ID="24.04"\nNAME="Ubuntu"\n',
        "home/demo/.config/i3/config": (
            "# Synthetic display customization; no real hardware is inspected.\n"
            "set $mod Mod4\n"
            "exec --no-startup-id ~/.local/bin/display-layout\n"
            "bindsym $mod+d exec dmenu_run\n"
            "bar {\n    status_command i3status\n}\n"
        ),
        "home/demo/.bashrc": 'source "$HOME/.config/demo/aliases"\n',
        "home/demo/.config/demo/aliases": "alias ll='ls -la'\n",
        "home/demo/.local/bin/display-layout": (
            "#!/bin/sh\n"
            "# Example only: output names require review on a destination machine.\n"
            "xrandr --output eDP-1 --auto\n"
        ),
        "home/demo/.config/systemd/user/low-battery.timer": (
            "[Unit]\nDescription=Synthetic low battery reminder schedule\n\n"
            "[Timer]\nOnBootSec=2m\nOnUnitActiveSec=5m\n"
            "Unit=low-battery.service\n\n[Install]\nWantedBy=timers.target\n"
        ),
        "home/demo/.config/systemd/user/low-battery.service": (
            "[Unit]\nDescription=Synthetic battery reminder\n\n"
            "[Service]\nType=oneshot\n"
            "ExecStart=/home/demo/.local/bin/low-battery\n"
        ),
        "home/demo/.local/bin/low-battery": (
            "#!/bin/sh\n"
            "# Synthetic placeholder, not a working battery monitor.\n"
            "notify-send 'Demo reminder' 'Battery check would run here'\n"
        ),
        "etc/cron.d/demo-backup": (
            "# Synthetic job: this file is never installed in the live scheduler.\n"
            "0 2 * * * demo /home/demo/.local/bin/backup-notes\n"
        ),
        "home/demo/.local/bin/backup-notes": (
            "#!/bin/sh\n"
            "# Synthetic placeholder, not a working backup implementation.\n"
            "printf '%s\\n' 'A selected notes backup would run here'\n"
        ),
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")

    snapshot = scan(
        ScanContext(root=root, home=home, platform="linux", distro="ubuntu"),
        name="synthetic-demo", now="2026-09-26T00:00:00+00:00",
    )
    Store(state).save(snapshot)
    score = coverage(snapshot.checks)
    (output / "report.json").write_text(
        json.dumps({"snapshot": asdict(snapshot), "coverage": score}, indent=2,
                   sort_keys=True) + "\n", encoding="utf-8",
    )
    timer = next(f for f in snapshot.findings
                 if f.module == "systemd" and f.title.endswith("low-battery.timer"))
    state_arg = shlex.quote(str(state))
    readme = f"""# Driftstamp synthetic demo

This directory represents a fictional Ubuntu+i3 machine. It contains no copied
user settings, live service state, credentials or real hardware observations.
All configuration and scripts were generated here. Scripts were never executed,
services were never activated, and cron jobs were never installed.

The saved snapshot uses a fixed timestamp. It contains {len(snapshot.findings)}
findings and {score['completed']}/{score['total']} completed declared checks
({score['percent']}%). This is checklist completion, not the percentage of all
possible customizations discovered. Unsupported use cases remain visible.

Contents:
- `root/`: synthetic filesystem, with a fictional `/home/demo`.
- `state/`: saved `synthetic-demo` snapshot, ready for CLI browsing.
- `report.json`: snapshot and declared-check coverage report.

From the Driftstamp project directory, these Bash commands inspect saved data:

```bash
python3 -m driftstamp --state-dir {state_arg} list
python3 -m driftstamp --state-dir {state_arg} coverage
python3 -m driftstamp --state-dir {state_arg} show {timer.id}
```

The timer finding groups its local service and battery script. Other findings
demonstrate i3 launch references, a sourced shell file and a cron script.
Dependencies inside script bodies are intentionally not inferred; inspect the
warnings before migration. No findings have been selected for export.

To reproduce, run `examples/demo.py --output NEW_DIRECTORY` again. Use a new
directory: existing output is never overwritten. Absolute fixture paths will
differ, but logical resource URIs, finding IDs, input text and timestamp remain
stable.
"""
    (output / "README.md").write_text(readme, encoding="utf-8")
    print(f"Synthetic demo saved to {output}")
    print(f"Findings: {len(snapshot.findings)}")
    print(f"Declared-check coverage: {score['percent']}% "
          f"({score['completed']}/{score['total']})")
    print("This measures declared checks, not all tweaks on a real machine.")
    print(f"Browse: python3 -m driftstamp --state-dir {state_arg} list")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
