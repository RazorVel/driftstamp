# Local validation — 2026-09-27 (0.1.2)

Environment: Linux, Python 3.12.3. Machine roots and scan state were synthetic
fixtures. No live home or system configuration was scanned or changed.

| Validation | Result |
| --- | --- |
| Core, collectors, CLI, manual, installer and regression suite | 190 tests passed, no skips |
| External Red Hat plugin example | 5 tests passed, no skips |
| Added regression tests since 0.1.1 | 103 tests across collectors, engine/plugins, migration, state/context/CLI and installer |
| Synthetic Ubuntu/i3 demo | 8 findings; 15/18 declared checks complete (83.3%) |
| Installed CLI workflow outside source directory | Scan, show, review, rescan, list, diff, export, Ubuntu/Arch plans, snapshots, modules, coverage passed |
| User installer | Private environment, prefix containing spaces, command link, manual and repeat installation verified |
| Failed installer update | Existing command/environment and manual preserved; failed candidate removed (injected regression) |
| Manual freshness/rendering | Generated page matches parser/version; groff and man verification passed |
| Offline build with setuptools 68.1.2 | 0.1.2 wheel and source distribution; matching manual and MIT licenses |

Commands, from the project directory:

```sh
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s examples/redhat_plugin/tests -v
python3 tools/build_manpage.py --check
python3 examples/demo.py --output ./demo-output-0.1.2
python3 -m driftstamp --state-dir ./demo-output-0.1.2/state coverage
python3 -m pip wheel --no-deps --no-build-isolation --no-index --no-cache-dir --wheel-dir dist .
python3 tools/install_user.py dist/driftstamp-0.1.2-py3-none-any.whl --prefix /tmp/driftstamp-install-check
/tmp/driftstamp-install-check/bin/driftstamp --version
man -M /tmp/driftstamp-install-check/share/man driftstamp
```

Use a fresh demo output directory; existing outputs are intentionally preserved.
Building a wheel needs provisioned setuptools and wheel. The runtime, tests and
demo have no third-party dependencies. Installation additionally needs venv and
ensurepip. The user installer is POSIX-specific; its installation tests explicitly
skip on Windows while the non-POSIX rejection and portable checks remain tested.
The manual formatter test explicitly skips where groff is unavailable.

The new regressions cover:

- Malformed saved snapshots, pointers and review data; failed writes, note
  retention, nonexistent homes, symlink loops and exclusion aliases.
- Plugin metadata/results, reserved IDs, preserved capability gaps, reused plugin
  objects, and comparisons across changes in platform, distro and scope.
- Quoted paths, env shebangs, cron macros, systemd timer overrides and Exec resets;
  unsupported execution context and inherited drop-ins remain visible gaps.
- Bundle path/metadata consistency, checksums, link records, malformed destination
  plugins, failed export cleanup, deduplication, and destination type conflicts.
- User installation collisions, symlink ownership, failed upgrades and successful
  switching while retaining previous environments.

## Executed-line measurement

Python's standard `trace` module was run over the 190-test suite:

```sh
python3 -m trace --count --summary --missing --coverdir /tmp/driftstamp-coverage --ignore-dir /usr --module unittest discover -s tests -q
```

Selected module results: CLI 84%, context 92%, engine 94%, migration 93%, registry
98%, store 96%; collectors ranged from 79% to 100%. These are executable-line
measurements, not branch coverage, discovery recall or proof that every edge case
has been tested. The separate installed workflow exercises real packaging and
installation paths in addition to the unit suite.

The demo's unsupported checks cover package configuration baselines, arbitrary
application preferences and ACL/capability/ownership comparisons. Its 83.3%
score measures completion of that declared checklist, not the fraction of all
tweaks discovered or a migration success probability.

The prototype's supported discovery/review/export/plan workflow is ready for an
initial user experience trial. Native Windows discovery, RPM baselines, automatic
restoration and an actual Ubuntu-to-Arch rebuild remain outside this release.
Other Python/OS combinations are configured in CI but have not been run here.
No calibrated accuracy on labeled real machines or destination package
availability is claimed.
