"""i3 configuration candidates and statically written references."""
import re
import shlex

from driftstamp.model import ModuleSpec
from .common import Collector, command_references, literal_reference, make_finding, scoped


class I3Module:
    spec = ModuleSpec("i3", "i3 configuration and literal startup references", ("linux",), (
        ("i3.user", "Inspect conventional user i3 configuration locations"),
        ("i3.system", "Inspect /etc/i3/config"),
        ("i3.references", "Inspect literal includes, exec commands and bar commands"),
    ))

    def scan(self, ctx):
        c = Collector(ctx, self.spec)
        scoped(ctx, c, "i3.user", "i3.system")
        sources = []
        if ctx.scope != "system":
            sources += [(ctx.home / ".config/i3/config", "i3.user"), (ctx.home / ".i3/config", "i3.user")]
        if ctx.scope != "user":
            sources.append((ctx.system("etc/i3/config"), "i3.system"))
        for path, check in sources:
            for path in c.candidates(path, check):
                resources, evidence, dependencies = [], [], []
                visited = set()

                def inspect(config, discovery_check):
                    if config in visited:
                        return
                    if len(visited) >= 64:
                        c.problem("i3.references", "Include traversal limit reached")
                        return
                    visited.add(config)
                    text = c.read(config, discovery_check)
                    if text is None:
                        return
                    resources.append(c.resource(config, discovery_check))
                    evidence.append(f"i3 configuration candidate: {ctx.uri(config)}")
                    for line in text.splitlines():
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if line.startswith("include "):
                            try:
                                values = shlex.split(line)[1:]
                            except ValueError:
                                c.problem("i3.references", f"Malformed include in {config}")
                                continue
                            for value in values:
                                if any(char in value for char in "*?["):
                                    c.problem("i3.references", f"Glob include requires review in {config}: {value}")
                                    continue
                                target = literal_reference(c, value, config.parent, "i3.references", resources, evidence)
                                if target:
                                    inspect(target, "i3.references")
                        else:
                            # Keep the command spelling intact: splitting and
                            # joining words changes spaces inside quoted paths.
                            directive = re.match(r"^(exec|exec_always|status_command|i3bar_command)\s+(.*)$", line)
                            if not directive:
                                directive = re.match(r"^(?:bindsym|bindcode)\s+(?:--\S+\s+)*\S+\s+(exec)\s+(.*)$", line)
                            if directive:
                                command = re.sub(r"^--no-startup-id(?:\s+|$)", "", directive.group(2))
                                command_references(c, command, config.parent, "i3.references", resources, evidence, dependencies)
                inspect(path, check)
                if any(resources):
                    c.result.findings.append(make_finding(ctx, "i3", path, "i3 configuration", "Configuration candidate with statically visible launch references.", resources, evidence, dependencies, ["i3 variables, glob includes and shell expressions are not evaluated.", "Presence does not establish which i3 configuration is active."]))
        return c.finish()
