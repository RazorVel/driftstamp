# Driftstamp

**Rediscover the changes that make a machine yours.**

Driftstamp is a local CLI for finding forgotten customizations, keeping notes
about their purpose, and preparing a reviewed migration bundle. The first
implementation focuses on Ubuntu/i3 and proposes Ubuntu or Arch rebuild steps.
Discovery modules and destination adapters are independently extensible.

This is a working **0.1.2 prototype**, not an exhaustive installation-history
reconstructor or unattended restore tool. On an existing machine without a
baseline, it finds configuration candidates and literal connections. It does
not claim every candidate was authored by you or differs from a vendor default.

## Install for your user (Linux)

From the project directory, build a wheel and install it without changing system
Python or requiring sudo. The build needs Python 3.11+, pip, setuptools 68+ and
wheel already available; the installer needs Python's `venv`/`ensurepip` support.
These commands do not download dependencies:

```sh
python3 -m pip wheel --no-deps --no-build-isolation --no-index --no-cache-dir --wheel-dir dist .
python3 tools/install_user.py dist/driftstamp-0.1.2-py3-none-any.whl
driftstamp --version
man driftstamp
```

The installer creates a private environment below
`~/.local/share/driftstamp/environments/env-<id>/`,
links `~/.local/bin/driftstamp`, and installs the manual under
`~/.local/share/man/man1`. Make sure `~/.local/bin` is on your shell's PATH.
Use `man -M ~/.local/share/man driftstamp` if your manual search path omits it.
An unrelated existing command is preserved and causes an installation error.
Rerun the installer with a new wheel to update: each environment is verified
before switching the command link. Earlier environments remain available;
the installer does not delete them. `--prefix PATH` selects another user prefix.

Start with:

```sh
driftstamp modules
driftstamp scan --scope user --name first-look
driftstamp list --needs-review
driftstamp coverage
driftstamp show FINDING_ID
```

Use a real ID from `list`. Scan names are immutable; choose another name on
later runs or omit `--name` for an automatically generated name. A scan that
exits with status 3 still saves its results; read `coverage` to see the gaps.

## Run without installation

Python **3.11+** is the only runtime and test requirement. From this directory:

```sh
python3 -m driftstamp --help
python3 -m driftstamp modules
```

Run a synthetic Ubuntu/i3 demonstration that never inspects your real home:

```sh
python3 examples/demo.py --output ./demo-output
python3 -m driftstamp --state-dir ./demo-output/state list
python3 -m driftstamp --state-dir ./demo-output/state coverage
```

On Windows, use `py -3` in place of `python3`. The CLI, records and tests are
designed for cross-platform use, but native Windows discovery is **not yet
implemented**. The CI matrix is configured; a local Linux test run is not proof
of passing Windows CI.

Optionally install into an isolated Python environment with `python3 -m pip
install .`; this exposes the `driftstamp` command. Installation uses setuptools
as a build dependency. Running the source and unit tests requires no package
installation, third-party test framework, network, root privileges, or desktop
session.

## Manual page

The package includes **driftstamp(1)** under the Python installation prefix's
`share/man/man1` directory. To install just the manual for your user:

```sh
make install-man PREFIX="$HOME/.local"
man -M "$HOME/.local/share/man" driftstamp
```

If that directory is already in your manual search path, `man driftstamp` is
enough. A virtual-environment installation places the page under that
environment's `share/man`; use `man -M /path/to/venv/share/man driftstamp` or the
separate installation command above. The manual can also be read directly from
the source checkout:

```sh
man -l man/driftstamp.1
```

System packages can stage the manual with
`make install-man PREFIX=/usr DESTDIR=/path/to/package-root`.
`MANDIR` may override the full manual directory. Installing the page alone does
not install the CLI. Source and wheel distributions include the page and license.

The command reference is generated from the actual CLI parser. Whenever commands,
flags, help text, or the version change, regenerate and verify it:

```sh
make man
make check-man
```

Without Make, use `python3 tools/build_manpage.py` and append `--check` to verify.
The test suite and CI fail if the committed manual is missing or stale. Edit
`driftstamp/manpage.py` for explanatory sections; do not hand-edit the generated
`man/driftstamp.1`. See [the changelog](CHANGELOG.md) for release changes.

## Workflow

```sh
driftstamp scan --name laptop-before-cleanup
driftstamp list --needs-review
driftstamp show FINDING_ID
driftstamp review FINDING_ID --decision keep --note "Needed for my desk setup"
driftstamp export --output ./my-setup
driftstamp plan ./my-setup --target arch
```

Use an actual ID from `list`. Source files are read, never changed by these
commands. Snapshots and review decisions are written only to Driftstamp's state
directory; exports go to the explicitly chosen output directory. Plans do not
install packages, enable services, create links, or modify destination settings.

| Command | Behavior |
| --- | --- |
| `scan` | Inspect supported locations and save a new immutable snapshot |
| `list` | Filter a saved inventory without rescanning |
| `show ID` | Show evidence, resources, dependencies, and limitations |
| `review ID` | Record `undecided`, `keep`, `skip`, or `investigate` and a note |
| `diff OLD [NEW]` | Compare fingerprints; default NEW is latest |
| `coverage` | Show completed checks, the denominator, and every gap |
| `modules` | List discovery modules and their declared checks |
| `snapshots` | List saved snapshot names |
| `export --output PATH` | Copy verified content from findings marked keep |
| `plan BUNDLE --target NAME` | Produce a destination-specific review plan |

Common flags, accepted before or after a subcommand:

- `--state-dir PATH`: isolated snapshot/review storage.
- `--format text|json`: readable text or stable structured output.
- `--allow-plugins`: opt into installed third-party module/target code.
- `--no-color`: plain output (currently the default for all text).

Scan flags:

```sh
driftstamp scan --scope user --module i3 --module systemd
driftstamp scan --include ~/scripts --exclude '~/scripts/old/**'
driftstamp scan --root ./fixture/root --home ./fixture/root/home/alice --distro ubuntu
```

`--scope` is `user`, `system`, or `all` (default). `--module`, `--include`, and
`--exclude` are repeatable. Directory exclusions also apply to referenced children
and resolved symlink targets. Includes add script discovery locations; they do not
select content for migration. Fixture roots require an explicit contained home.
User/all scans require an existing home directory. `--home` selects the actual
home path; a Linux sudo invocation resolves the
original caller's home instead of silently switching to root's. Prefer ordinary
user scans; blocked system locations are reported visibly.

`list` supports `--scan NAME`, `--module ID`, `--decision STATE`,
`--min-confidence 0..100`, and `--needs-review`. `show`, `coverage`, and `export`
also support `--scan NAME`. Review decisions persist independently across scans.
An older snapshot can be inspected, but decisions are made against the latest
inventory and export always verifies the current source files against the
selected snapshot.

`plan --check-host` validates the current machine against the chosen destination
and inspects file conflicts. `--release VERSION` records the desired release;
the initial package mappings are **not release-specific** and repository
availability is not verified. Unknown dependencies remain unresolved. Package
installation is displayed as an argument list, never executed.

Exit codes: **0** successful operation; **1** runtime/input-data failure;
**2** invalid CLI arguments; **3** scan saved with blocked or partial checks.
Unsupported or explicitly excluded areas lower coverage but do not themselves
cause code 3. Diagnostics go to stderr; JSON remains parseable on stdout.

## Current discovery scope

| Module | Implemented | Important limits |
| --- | --- | --- |
| `i3` | Conventional user/system configs, literal includes and launch references | No active-config detection, variable/glob evaluation or arbitrary shell execution |
| `shell` | Conventional Bash/Zsh profiles and literal sourced files | No shell evaluation or full script dependency analysis |
| `scripts` | Conventional local script directories and explicit includes | Excludes binary/dependency noise; directory presence is not authorship proof |
| `systemd` | Local unit files/drop-ins, timer/service grouping, literal script references | No active-state query, environment expansion, or automatic activation |
| `cron` | Conventional cron files/spools and literal script references | Access and dynamic command gaps are reported |

Reads are bounded to regular files of 1 MiB and traversals to 2,000 entries per
root. Limits, access failures, and exclusions cannot silently become completed
checks. Directory symlinks are not traversed. Source commands are never executed.

Pending areas remain visible in coverage: package configuration baselines,
arbitrary application settings, and ACL/capability/ownership comparisons. Native
PowerShell, CMD, Windows services/tasks, and RPM/Red Hat-specific collectors are
future modules. A Red Hat extension **example** demonstrates the plugin contract
without pretending to inspect RPM.

Exports include selected files, hashes, mode metadata, link metadata, evidence,
and notes. They reject stale or missing source files rather than silently
exporting a different state. Links are recorded, not created. This first release
does not migrate ACLs, ownership, arbitrary registry resources, or all indirect
dependencies. File contents are not redacted; inspect selections before sharing
a bundle. Export is a local operation with no upload.

## Confidence and coverage

These are three separate measurements:

- **Finding confidence:** an uncalibrated 0–100 evidence-strength score, with
  reasons. It is not the probability that you authored a file.
- **Inspection completion:** completed declared checks divided by all applicable
  checks, including unsupported, excluded, blocked and partial checks. Partial
  checks receive zero completion credit.
- **Tests:** reproducible software behavior checks; passing tests do not measure
  the percentage of all real-world tweaks discovered.

The synthetic example currently completes **15 of 18 declared checks (83.3%)**.
The three unsupported areas above account for the gap. This is a measured demo
result within a bounded checklist, not estimated real-machine recall. Actual
scans may score lower because of exclusions, access errors, or unresolved syntax.

See [the scoring rules and test matrix](docs/COVERAGE.md).

## Stack and extensions

- **Python 3.11+**, type-annotated dataclasses and protocols by convention.
- **argparse** for a predictable CLI usable from Bash, CMD, or PowerShell.
- **Versioned JSON** for snapshots and manifests; SHA-256 file fingerprints.
- **Python package entry points** for opt-in discovery and target extensions.
- **unittest** with temporary filesystems, fixed clocks and fake plugin metadata.

Python was chosen for its filesystem tooling, cross-platform runtime and small
plugin authoring overhead. The core has no runtime dependencies. Discovery uses
`driftstamp.modules`; destination planning uses `driftstamp.targets`. This uses
the [standard Python entry-point mechanism](https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/).

Read [the architecture](docs/ARCHITECTURE.md), [plugin authoring guide](docs/PLUGINS.md),
and [Red Hat extension example](examples/redhat_plugin/README.md).

## Reproduce verification

```sh
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s examples/redhat_plugin/tests -v
```

Fixtures cover Linux collectors, persistence, comparison, confidence arithmetic,
plugin failure isolation, export integrity, target planning and the complete CLI
workflow. No test depends on the developer's actual home, package database,
network, sudo, or running desktop. Permission failures are injected; symlink
tests explicitly skip when host support is unavailable.

When `groff` is installed, an additional test checks the rendered manual for
formatter warnings; otherwise that optional test is explicitly skipped.

GitHub Actions is configured for Linux and Windows with Python 3.11–3.14.
This repository has not been published and those CI jobs have not run yet.

## License

Driftstamp and the included example extension are licensed under the
[MIT License](LICENSE). Copyright (c) 2026 Driftstamp contributors.
