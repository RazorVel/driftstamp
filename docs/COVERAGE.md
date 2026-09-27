# Evidence, inspection coverage, and tests

Driftstamp keeps three different questions separate:

1. How strong is the evidence for an individual finding?
2. How much of the declared inspection scope was completed?
3. Which software behaviors have reproducible tests?

None of these alone measures the fraction of all real customizations discovered.
Measuring discovery accuracy would require a labeled collection of machines or
fixtures with known customizations, then separate precision and recall results.
No such accuracy percentage is claimed for this initial implementation.

## Finding evidence strength

Each finding has an integer `confidence` from 0 to 100, interpreted as an
**evidence-strength score**, not a statistical probability. It is assigned by the
collector and must be accompanied by concrete evidence and any known ambiguity.
Do not render `80` as “80% likely to have been written by you.”

Useful evidence includes a local service referring to a local script, a supported
literal include, or a verified difference from a vendor reference. Merely being
unowned by a package or living in a conventional script directory is weaker
evidence. Neither proves authorship or intent.

There is no calibrated cross-module scoring model yet. Scores help sort a
module's findings; users should inspect the reasons before comparing scores
across modules. A future rubric should be versioned and checked against labeled
examples. The core validates score bounds; it does not manufacture certainty
by averaging collectors' opinions.

## Declared-use-case inspection coverage

Each module declares testable checks before scanning, such as configuration
discovery and literal-reference inspection. Each check reports one status:

| Status | Meaning | Credit |
| --- | --- | --- |
| `complete` | The declared check finished within its documented scope | Full weight |
| `partial` | Some applicable work could not be completed | Zero |
| `blocked` | Access, input, or another failure prevented inspection | Zero |
| `unsupported` | The platform or capability is not implemented | Zero |
| `excluded` | A scope or selection rule left it uninspected | Zero |
| `not_applicable` | The check demonstrably does not apply | Omitted |

```text
inspection percent =
  100 × sum(weights of complete checks)
      / sum(weights of all checks except not_applicable)
```

Check weights are declared importance weights, not measured probabilities. When
all weights are one, this is simply completed checks divided by applicable
declared checks. An empty denominator produces `null`, not 100%.

For illustration only, six complete checks, two unsupported checks, one blocked
check, and one excluded check with equal weights produce **60%**. This is not a
benchmark result. A filesystem permission failure cannot improve the score by
removing the blocked check from the denominator.

Missing check results are surfaced by the engine. An unsupported capability or a
deliberately unselected applicable module remains visible as a gap. Linux-only
domains such as i3 can be inapplicable on Windows; separate Windows capability
checks remain unsupported. `not_applicable` must not be used to disguise missing
platform capabilities, exclusions, or parser limits.
Reading no findings may still be complete if the check successfully established
that its supported candidate locations were absent.

Always show the score beside status counts, individual check details, warnings,
and the declared scope. A 100% result means that these checks completed; it does
not mean the whole operating system has been exhaustively inspected.

The first scope omits package-baseline comparisons unless a working collector
is installed. Application settings beyond the shipped modules, dynamic shell
behavior, deleted historical changes, and unsupported operating systems remain
explicit limitations. A platform-wide completeness claim would be misleading.

## Reproducible test requirement matrix

This is an acceptance matrix, not a test-results report. The repository's actual
test run determines which requirements pass. “Planned” behavior is not current
functionality merely because it appears here.

| Area | Required deterministic scenario | Expected property |
| --- | --- | --- |
| i3 | Config, literal include, referenced script in a temporary home | Files and evidence connected; discovered text never executed |
| Shell | Profile sources another local file; unsupported substitution | Literal source found; dynamic reference reported as unresolved |
| Scripts | Known script directory plus an explicit include and exclude | Stable ordering; excluded data not read |
| systemd | Timer, corresponding service, literal script reference | Local unit resources and referenced script found |
| Cron | Local cron entry and literal script | Configuration identified without launching the job |
| Scope | Same fixture scanned with user/system/module restrictions | Omitted checks appear as excluded |
| Boundaries | Symlink escaping the fixture root | No outside read; limitation surfaced |
| Input limits | Oversized or non-text file; bounded traversal | Failure visible; completion not falsely claimed |
| Evidence | Invalid confidence values | Invalid scores rejected |
| Inspection score | All statuses, weighted checks, empty denominator | Formula above preserved; unsupported/excluded remain in denominator |
| Snapshots | Same fixture and injected clock | Reproducible serialized content and stable finding identities |
| Comparison | Modified file, unscanned module, and dropped explicit include | Change reported; scope gap not mislabeled as deletion |
| Reviews | Keep/note decision followed by a new snapshot | Stable identity retains the decision |
| Export | Kept, skipped, changed, missing, and unsupported resources | Selected valid content only; stale/unsupported selections fail visibly |
| Bundle validation | Altered hash and path traversal attempt | Tampering and out-of-bundle reads rejected |
| Plan | Known and unknown commands for both initial targets | Supported mappings proposed; unknown dependencies stay unresolved |
| Plugins | Fake entry points, duplicate IDs, bad API, load failure, malformed result | Explicit opt-in; broken plugin produces one failure without corrupting the snapshot |
| CLI | Isolated state directory and temporary machine root | Predictable JSON, diagnostic streams, and exit statuses |
| Windows growth | Linux-only module invoked for Windows | Inapplicable Linux domain plus unsupported Windows checks; no implied Windows discovery |
| Red Hat example | Example extension instantiated without RPM | Unsupported check; no invented package evidence |

Unit tests should use `TemporaryDirectory`, fixed data and clocks, and faked
entry-point metadata. They must not require the developer's installed packages,
active i3 session, root privileges, network, or personal files. Permission and
symlink tests should be capability-skipped where the platform cannot reproduce
their prerequisites; skipped tests must remain visible in results.

Run the main suite from the project directory:

```sh
python3 -m unittest discover -s tests -v
```

The external module example has its own isolated suite:

```sh
python3 -m unittest discover -s examples/redhat_plugin/tests -v
```

Line coverage, if measured later with optional tooling, belongs in a separate
software-test report. It must not be presented as machine inspection coverage
or as evidence that a migration succeeds on a real destination. Cross-platform
CI results likewise remain separate from local test results.
