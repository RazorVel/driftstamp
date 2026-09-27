# Extending Driftstamp

Driftstamp extensions are ordinary Python packages. They are unrelated to Codex
plugins. Installing an extension and opting into loading it allows its Python
code to run locally; entry points are an extensibility mechanism, not a sandbox.
Load only extensions you trust.

External loading is disabled by default. Pass `--allow-plugins` for operations
that need installed extensions:

```sh
driftstamp --allow-plugins modules
driftstamp --allow-plugins scan --module example-rhel-config
```

The same flag is accepted after the subcommand. Built-in modules do not require
this flag.

## Discovery module API v1

A module class has a `ModuleSpec` in `spec` and a `scan(ctx)` method returning
`ModuleResult`. Register the class under the `driftstamp.modules` entry-point
group. The included example uses:

```toml
[project.entry-points."driftstamp.modules"]
example-rhel-config = "driftstamp_redhat_example:RedHatExample"
```

The spec declares a unique stable module ID, title, supported platforms, check
IDs and descriptions, and `api_version=1`. Check IDs must match the declaration
and start with the module ID followed by a dot. Module IDs `plugins` and
`platform` are reserved for core diagnostics. Duplicate IDs, invalid record
types, and incompatible API versions are rejected. Loading failures are
reported as warnings. A plugin implementing a pending capability must declare
its exact check ID; a similarly named module cannot hide an unsupported gap.

`scan(ctx)` returns:

- Findings containing stable IDs, resources, evidence, scores, dependencies, and
  migration warnings.
- Check results describing completed work and gaps.
- Module-level warnings where appropriate.

Use `finding_id(module_id, stable_key)` for identities and the context's URI
mapping for resources. Do not use timestamps or file contents as identity keys.

Read through `ScanContext`: `system()`, `allowed()`, `read_text()`, `resource()`,
`walk_files()`, and `resolve_reference()` provide the common filesystem and
reference rules. Do not bypass these helpers to read outside a fixture root or
ignore a user's exclusions. They do not sandbox malicious extension code.

Do not execute discovered shell text. A collector must report unresolved dynamic
references instead of guessing, and a failed read must not become a silently
successful empty result. Report every declared check. Scope restrictions yield
`excluded`; unsupported functionality yields `unsupported`.

See [the implementation contract](IMPLEMENTATION_CONTRACT.md) for the precise
dataclass signatures and context limits.

## Red Hat example

[`examples/redhat_plugin`](../examples/redhat_plugin) is an installable extension
with a deliberately unsupported RPM configuration check. Its purpose is to
demonstrate packaging, API declarations, reproducible unit tests, and honest
reporting of an unimplemented capability. It does **not** provide RPM inventory
or package-baseline comparison.

From the repository root, in a Python environment where Driftstamp and a
compatible setuptools build backend are already installed:

```sh
python3 -m pip install --no-build-isolation --no-deps ./examples/redhat_plugin
driftstamp --allow-plugins modules
driftstamp --allow-plugins scan --module example-rhel-config --distro rhel
```

The installation command intentionally does not fetch dependencies; the build
backend must be provisioned beforehand. The example's tests run directly from
source and do not require installation or network access.

To turn the example into a real collector:

1. Define the exact RPM-related inspection claims and fixture formats.
2. Add an isolated package-metadata provider with a fake provider for tests.
3. Compare configuration against a verified reference for the installed package
   version, preserving missing-reference and generated-file uncertainty.
4. Return actual resource evidence and complete only the checks that succeeded.
5. Test package upgrades, local edits, missing baselines, access failures, and
   exclusions before advertising RPM support.

Avoid simply labeling every unowned file a user customization. Package ownership
and content differences are evidence, not authorship attribution.

## Destination extensions

Destination adapters use the separate `driftstamp.targets` entry-point group.
They map supported dependencies and migration requirements to proposed steps;
they must not execute installation or restore operations during planning.
External target loading is also opt-in.

The source collector example does not demonstrate a target adapter. Consult the
current `driftstamp.migration` implementation when implementing one. API v1 is:

```python
from driftstamp.migration import TargetSpec

class ExampleTarget:
    spec = TargetSpec(
        id="example-target",
        title="Example destination",
        platforms=("linux",),
        api_version=1,
    )

    def resolve(self, command: str, release: str | None = None) -> str | None:
        # Return a destination package name, or None for an unresolved command.
        return None

    def install_argv(self, packages: list[str]) -> list[str]:
        # Return display-only installation arguments; never execute them here.
        return []

    def resource_action(self, resource: dict, finding: dict) -> tuple[str, str]:
        return "review", "No automated migration rule is available."
```

This is a conservative interface illustration, not an installed target. Register
the no-argument class or factory in the `driftstamp.targets` entry-point group.
The loader checks the API version, ID uniqueness, and all three methods.

`platforms` currently declares the **source platforms the adapter can migrate**.
It does not grant automatic cross-OS translation. `resolve()` receives each
declared command dependency and the optional destination release; return `None`
when no validated mapping is available. Returned package names must start with a
letter or number and contain only letters, numbers, `+`, `.`, `_`, `:`, or `-`.
`install_argv()` receives sorted, unique package names and returns a list of
arguments for display. No shell text is executed by the core.

`resource_action()` receives a validated file record and an associated finding
dictionary. For a resource shared by several findings, the core combines their
warnings. Return an action from `copy`, `adapt`, `merge`, `review`, or `omit`,
plus a plain-language reason. With `--check-host`, actual destination conflicts
can override the proposed action.

Keep source and destination support independently testable. Supporting RPM
discovery does not itself make Red Hat migration available. The core does not
validate repository availability or release compatibility on an adapter's behalf.

## Future Windows extensions

Windows support requires dedicated collectors and destination behavior. Start
with profiles and literal references for PowerShell or CMD, then add registry,
services, and scheduled-task resources with explicit semantics. Avoid filesystem
assumptions such as POSIX ownership or executable modes in the shared model.

Every new resource kind needs export and planning support before it can be
migrated. A collector may report an unsupported resource with a useful warning,
but it must not disguise that resource as a regular file just to pass export.

## Extension test rules

- Create all machine and state fixtures in temporary directories.
- Inject metadata, clocks, and platform conditions; do not read the host's live
  package database or start services.
- Assert both findings and check statuses. Empty findings alone cannot
  distinguish success from failed discovery.
- Exercise platform mismatch, exclusions, unreadable inputs, and unsupported
  dynamic syntax as well as successful examples.
- Keep IDs stable when fixture paths change but logical resource identity does
  not, and when file contents change after a review decision.

Primary references: [package entry points](https://packaging.python.org/en/latest/specifications/entry-points/)
and [Python importlib.metadata](https://docs.python.org/3/library/importlib.metadata.html).
