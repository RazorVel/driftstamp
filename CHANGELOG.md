# Changelog

## 0.1.2 — 2026-09-27

- Add regression coverage for malformed state, plugin contracts, collector
  parsing, migration metadata, scan boundaries, and failed writes.
- Reject missing user homes and malformed snapshot data; prevent exclusions
  from being bypassed through direct references or symlink aliases.
- Avoid false deletion reports after platform, distro, or capability changes;
  retain unsupported checks until their exact capability is implemented.
- Preserve quoted script paths and handle simple systemd overrides, env
  shebangs, and unsupported syntax without false completion claims.
- Validate destination plugin responses and bundle metadata; flag symlink and
  file-type destination conflicts for review.
- Add a user installer for built wheels, private Python environments, command
  links, and the manual. Verify updates before switching the public command.
- Refresh the generated manual and document first-use commands.

## 0.1.1 — 2026-09-27

- Add `driftstamp(1)`, generated from the command parser and shipped in wheels
  and source distributions.
- Add `make install-man`, with `PREFIX`, `MANDIR`, and `DESTDIR` support.
- Check manual freshness in the test suite and CI; regenerate with `make man`.
- Add the MIT license to the project and example plugin, including distribution
  metadata and packaged license files.
- Use one version source for the CLI, package metadata, and generated manual.
- Clarify command help and document installation and maintenance.

## 0.1.0 — 2026-09-26

- Initial CLI foundation with five Linux discovery modules, snapshots, review
  decisions, exports, Ubuntu/Arch plans, and an external plugin example.
