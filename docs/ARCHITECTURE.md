# Driftstamp architecture

Driftstamp inventories the current evidence of machine customization, lets its owner
record what is worth keeping, and prepares a migration plan. Its name combines
configuration drift with a saved record in time. It is a project name, not a claim
of trademark or package-name availability.

An installation-time history cannot be reconstructed from a present-day scan.
The first scan is a baseline. Later snapshots can identify changes within the
areas actually inspected. Neither local file ownership nor a recent timestamp
proves that a person authored a change.

## Technology choices

- Python 3.11 or newer, with no third-party runtime dependencies.
- `argparse` for a scriptable CLI, dataclasses for the public model, and JSON for
  snapshots, review decisions, and bundle manifests.
- Python package entry points for optional extensions.
- Standard-library `unittest`, temporary filesystem fixtures, and injected
  timestamps for reproducible tests.

Python keeps platform inspection and small extension modules approachable for
system administrators. The core can run independently of a desktop session,
package manager, or a particular shell. JSON makes reports usable by other tools.
The tradeoff is a Python installation requirement; packaging as a standalone
executable can be added later without changing the discovery protocol.

## Responsibilities

```text
CLI
  ├── Engine + registry
  │     ├── Discovery modules
  │     └── Scan context: bounded reads, paths, exclusions, hashes
  ├── Snapshot store + review decisions
  ├── Inspection coverage calculation
  └── Migration
        ├── Verified file bundle
        └── Destination adapters → proposed steps and unresolved requirements
```

The **core** owns identifiers, schemas, persistence, extension loading, score
aggregation, snapshot comparison, and exports. It does not interpret i3 syntax
or assume that every resource is a Unix file.

A **discovery module** understands a source domain such as i3, shell startup
configuration, systemd units, cron, or package configuration. It receives a scan
context and returns findings plus explicit check results. It explains what it
observed, including unresolved references. It does not silently change the host
or execute discovered commands.

A **destination adapter** translates supported requirements into proposed setup
steps for the selected destination. A source script may require `xrandr`; the
package used to provide it depends on the distribution. Unknown dependencies
stay unresolved. An adapter is separate from a collector: discovering RPM
configuration changes and planning an RPM-based destination are different jobs.

The first adapters target Ubuntu and Arch. A future Red Hat adapter belongs in
the destination layer; RPM package inventory belongs in source discovery. Both
can be added without rewriting the CLI workflow. Exact dependency mappings
must be validated for the destination release; a general package mapping is not
a complete dependency solver.

## Resource model and schema evolution

Snapshots use an explicit `schema_version`; modules declare `api_version`.
Version 1 models a finding as:

```text
stable finding ID + module + title + summary
resources + evidence + evidence-strength score
dependencies + warnings + migration hint
```

A resource has a stable URI, original path, kind, and optional content hash,
mode, and symlink target. `home://.config/i3/config` is an identity relative to a
user's home, not an instruction to write to that path on another machine.
`system://etc/example.conf` identifies a system resource. The native source path
is recorded separately.

Resource identity must not depend on the content hash: editing a file should
produce a changed resource, not erase the user's review decision. Likewise,
finding identity should derive from a stable module-specific key rather than
human-readable titles or current contents.

`kind` and URI schemes leave space for future resources such as Windows Registry
values, scheduled tasks, and exported application settings. A possible future
`registry://...` URI is a design direction, not a supported restore format. A
new resource type requires documented identity, serialization, comparison,
validation, and migration semantics. The current file exporter must refuse
unsupported kinds instead of dropping them or treating them as paths.

Unknown schema and API versions are rejected. Additions that alter meaning
require an explicit migration or new version, not optimistic deserialization.

## Cross-platform growth

The initial discovery modules focus on Linux and the user's i3 environment.
Choosing a Windows platform does not make those modules compatible. Linux-only
domains can be marked inapplicable, while separate Windows capability checks
remain unsupported and visible in the inspection report.

Future extensions may cover:

| Source area | Candidate extension | Portability issue |
| --- | --- | --- |
| RPM configuration | RPM package/configuration collector | Vendor reference versus current configuration |
| PowerShell | Profile and literal script-reference collector | Different profile scopes, versions, modules |
| CMD | Batch script and startup-configuration collector | Dynamic expansion and command interpretation |
| Windows automation | Services and scheduled-task collector | Accounts, credentials, task definitions |
| Windows settings | Registry-backed settings collector | Value types, permissions, machine-specific data |

PowerShell and CMD are source domains, not target operating systems. A
PowerShell profile on Linux could become a supported domain independently of
Windows configuration discovery. Neither a Bash script nor an i3 configuration
automatically has a Windows equivalent. Preserve the original evidence and
report incompatibility rather than attempting an implicit translation.

## Discovery limits

Static discovery can follow supported literal paths and references. It cannot
reliably resolve arbitrary shell substitutions, dynamically generated commands,
or every application-specific configuration format. Unresolved references need
a warning and an appropriate incomplete check.

All reads go through a bounded scan context. The context limits file sizes and
directory traversal, applies exclusions, and prevents fixture scans from reading
outside their declared root. Symlink identity is retained; directory symlinks
are not recursively followed. Scope restrictions are reported rather than
quietly disappearing from the coverage denominator.

Snapshot comparison must distinguish actual removal from absence caused by
unscanned, unsupported, or incomplete areas. A later restricted scan cannot
prove that a previously observed file was deleted. Snapshots retain their scan
scope, including explicit discovery locations and exclusions. When that scope
changes or is unavailable, missing findings remain unobserved rather than being
declared removed.

## Migration boundary

Review decisions are separate from findings. `keep` selects an item for export;
`skip` preserves its inventory record while leaving it out of the bundle.
`investigate` records an unresolved decision.

An export verifies selected file hashes against the snapshot and refuses a
changed or missing resource. It preserves resource metadata and source evidence
in a manifest. Symlink targets are metadata, not newly created live links in the
bundle. Unsupported resource types fail visibly.

A plan is a reviewable set of proposed steps. Commands are represented as
argument arrays; no command is executed. System configuration often requires
merging, and usernames, device names, and absolute paths may need adaptation.
Automatic application, rollback, and secrets provisioning are later work.

Exports can contain sensitive configuration contents. The discovery evidence
and the reviewed selection should make it clear what is included; no report or
bundle should be sent elsewhere automatically.

## Reference points

- [Python package entry points](https://packaging.python.org/en/latest/specifications/entry-points/)
- [Python unittest](https://docs.python.org/3/library/unittest.html)
- [i3 configuration and command reference](https://i3wm.org/docs/userguide.html)
- [systemd-delta](https://www.freedesktop.org/software/systemd/man/latest/systemd-delta.html)

These describe useful underlying interfaces. They do not imply that every
documented capability is implemented by Driftstamp.
