"""Shell startup files and literal source references."""
import re
import shlex

from driftstamp.model import ModuleSpec
from .common import Collector, literal_reference, make_finding, scoped


class ShellModule:
    spec = ModuleSpec("shell", "Bash and Zsh startup configuration", ("linux",), (
        ("shell.user", "Inspect conventional Bash/Zsh user startup files"),
        ("shell.system", "Inspect conventional system shell startup files and profile.d"),
        ("shell.sources", "Follow standalone literal source and dot statements"),
    ))

    def scan(self, ctx):
        c = Collector(ctx, self.spec)
        scoped(ctx, c, "shell.user", "shell.system")
        paths = []
        if ctx.scope != "system":
            paths += [(ctx.home / name, "shell.user") for name in (".profile", ".bashrc", ".bash_profile", ".bash_login", ".zshrc", ".zprofile", ".zshenv", ".zlogin")]
        if ctx.scope != "user":
            paths += [(ctx.system(name), "shell.system") for name in ("etc/profile", "etc/bash.bashrc", "etc/zsh/zshrc", "etc/zsh/zprofile", "etc/zsh/zshenv")]
            paths += [(p, "shell.system") for p in c.candidates(ctx.system("etc/profile.d"), "shell.system", True)]
        for path, check in paths:
            if not c.candidates(path, check):
                continue
            resources, evidence, visited = [], [], set()

            def inspect(source, source_check):
                if source in visited:
                    return
                if len(visited) >= 64:
                    c.problem("shell.sources", "Source traversal limit reached")
                    return
                visited.add(source)
                text = c.read(source, source_check)
                if text is None:
                    return
                resources.append(c.resource(source, source_check))
                for line in text.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if re.match(r"^(source|\.)\s+", line):
                        try:
                            tokens = shlex.split(line, comments=True)
                        except ValueError:
                            c.problem("shell.sources", f"Malformed source statement in {source}")
                            continue
                        if len(tokens) != 2 or any(char in tokens[1] for char in "*?["):
                            c.problem("shell.sources", f"Complex source statement left unresolved in {source}")
                            continue
                        if not tokens[1].startswith(("/", "~/", "$HOME/", "${HOME}/")):
                            c.problem("shell.sources", f"Relative source depends on PATH or the working directory in {source}")
                            continue
                        target = literal_reference(c, tokens[1], source.parent, "shell.sources", resources, evidence)
                        if target:
                            inspect(target, "shell.sources")
                    elif re.search(r"(?:^|[;&\s])(source|\.)\s+", line):
                        c.problem("shell.sources", f"Conditional or compound source statement left unresolved in {source}")
            inspect(path, check)
            if any(resources):
                c.result.findings.append(make_finding(ctx, "shell", path, f"Shell startup: {path.name}", "Startup configuration candidate and literal sourced files.", resources, evidence or None, warnings=["Aliases and functions are preserved as file content; their dependencies and execution order are not interpreted.", "Custom ZDOTDIR and XDG_CONFIG_HOME locations require explicit discovery extensions."]))
        return c.finish()
