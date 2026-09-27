# Red Hat collector example

This installable Driftstamp extension demonstrates the `driftstamp.modules`
entry point and discovery API v1. **It does not inspect RPM packages.** It reports
an unsupported check so incomplete functionality remains visible in inspection
coverage. User-only scope reports the system check as excluded.

Run its reproducible tests from the Driftstamp repository root:

```sh
python3 -m unittest discover -s examples/redhat_plugin/tests -v
```

Tests create a temporary fixture root and read no live package metadata. They
run directly from source without installing this extension.

To try extension loading in an environment with Driftstamp and setuptools
already installed:

```sh
python3 -m pip install --no-build-isolation --no-deps ./examples/redhat_plugin
driftstamp --allow-plugins modules
driftstamp --allow-plugins scan --module example-rhel-config --distro rhel
```

The last command may still show other declared modules as excluded. Selecting a
module limits execution; it does not erase the declared scope from the report.

Replace the unsupported result with a real metadata provider, verified baseline
comparison, findings, and fixtures before describing this extension as RPM
support. A destination adapter is a separate extension and is not provided here.

See [the extension guide](../../docs/PLUGINS.md) and
[coverage interpretation](../../docs/COVERAGE.md).

Licensed under the [MIT License](LICENSE), like the main project.
