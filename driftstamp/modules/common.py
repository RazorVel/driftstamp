"""Conservative filesystem and literal-reference helpers; never execute input."""
from __future__ import annotations

import re
import shlex
from pathlib import Path

from driftstamp.model import Check, Finding, ModuleResult, finding_id


class Collector:
    def __init__(self, ctx, spec):
        self.ctx = ctx
        self.spec = spec
        self.result = ModuleResult()
        self.problems = {key: [] for key, _ in spec.checks}
        self.status = {key: "complete" for key, _ in spec.checks}

    def problem(self, check, message, status="partial"):
        self.problems[check].append(message)
        # Blocked/partial have precedence over deliberately excluded files.
        rank = {"complete": 0, "excluded": 1, "partial": 2, "blocked": 3}
        if rank.get(status, 2) > rank.get(self.status[check], 0):
            self.status[check] = status

    def candidates(self, path, check, recursive=False):
        if not self.ctx.allowed(path):
            self.problem(check, f"Excluded or outside scan boundary: {path}", "excluded")
            return []
        try:
            if recursive:
                return self.ctx.walk_files(path)
            if path.exists() or path.is_symlink():
                return [path]
            return []
        except (OSError, ValueError) as exc:
            self.problem(check, f"Cannot inspect {path}: {exc}", "blocked")
            return []

    def read(self, path, check):
        if not self.ctx.allowed(path):
            self.problem(check, f"Excluded or outside scan boundary: {path}", "excluded")
            return None
        try:
            return self.ctx.read_text(path)
        except (OSError, ValueError) as exc:
            self.problem(check, f"Cannot read {path}: {exc}", "blocked")
            return None

    def resource(self, path, check):
        if not self.ctx.allowed(path):
            self.problem(check, f"Excluded or outside scan boundary: {path}", "excluded")
            return None
        try:
            return self.ctx.resource(path)
        except (OSError, ValueError) as exc:
            self.problem(check, f"Cannot capture {path}: {exc}", "blocked")
            return None

    def finish(self):
        for key, detail in self.spec.checks:
            messages = self.problems[key]
            self.result.checks.append(Check(key, self.status[key], detail + ("; " + "; ".join(messages) if messages else "")))
        return self.result


def scoped(ctx, collector, user_check, system_check=None):
    if ctx.scope == "system":
        collector.problem(user_check, "User locations excluded by scope", "excluded")
    if system_check and ctx.scope == "user":
        collector.problem(system_check, "System locations excluded by scope", "excluded")


def unique_resources(resources):
    return list({r.uri: r for r in resources if r is not None}.values())


def make_finding(ctx, module, path, title, summary, resources, evidence=None,
                 dependencies=None, warnings=None, confidence=75):
    return Finding(
        id=finding_id(module, ctx.uri(path)), module=module, title=title,
        summary=summary, resources=unique_resources(resources),
        evidence=evidence or [f"Discovered at {ctx.uri(path)}"], confidence=confidence,
        dependencies=sorted(set(dependencies or [])), warnings=list(warnings or []),
        migration="review" if not path.is_relative_to(ctx.home) else "adapt",
    )


def literal_reference(collector, value, base, check, resources, evidence):
    path = collector.ctx.resolve_reference(value, base)
    if path is None:
        collector.problem(check, f"Unresolved dynamic reference in {base}: {value}")
        return None
    resource = collector.resource(path, check)
    if resource:
        resources.append(resource)
        evidence.append(f"Literal reference: {collector.ctx.uri(path)}")
    return path if resource else None


def command_references(collector, command, base, check, resources, evidence, dependencies):
    """Inspect an executable and literal interpreter script arguments only.

    Other arguments can be secrets, output paths or data; never treat every path
    token as a script. Shell programs are not executed or recursively interpreted.
    """
    try:
        tokens = shlex.split(command, comments=True)
    except ValueError:
        collector.problem(check, f"Malformed command in {base}")
        return
    if not tokens:
        return
    if re.search(r"[`|;&<>\n]|\$\(", command):
        collector.problem(check, f"Compound or dynamic command left unresolved in {base}")
        return
    while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        tokens.pop(0)
    if not tokens:
        return
    executable = tokens[0].lstrip("-+!:@")
    if ("$" in executable and not executable.startswith(("$HOME/", "${HOME}/"))) or "%" in executable:
        collector.problem(check, f"Dynamic executable left unresolved in {base}")
        return
    # Absolute system binaries are package requirements, not migration payloads.
    system_binary = executable.startswith(("/usr/bin/", "/usr/sbin/", "/bin/", "/sbin/"))
    if ("/" in executable or executable.startswith("~")) and not system_binary:
        if not executable.startswith(("/", "~/", "$HOME/", "${HOME}/")):
            collector.problem(check, f"Relative executable depends on an unknown working directory in {base}")
        else:
            path = literal_reference(collector, executable, base, check, resources, evidence)
            if path:
                content = collector.read(path, check)
                if content:
                    dependencies.extend(interpreter_dependency(content))
    else:
        dependencies.append(Path(executable).name)
    interpreter = Path(executable).name
    if interpreter == "env":
        collector.problem(check, f"env command indirection requires review in {base}")
    if interpreter in {"sh", "bash", "dash", "zsh", "python", "python3", "perl", "ruby"}:
        arguments = tokens[1:]
        if any(arg in {"-c", "-m", "-e"} or (arg.startswith("-") and "c" in arg[1:]) for arg in arguments):
            collector.problem(check, f"Interpreter expression left unresolved in {base}")
        else:
            script = next((arg for arg in arguments if not arg.startswith("-")), None)
            if script:
                if not script.startswith(("/", "~/", "$HOME/", "${HOME}/")):
                    collector.problem(check, f"Relative script depends on an unknown working directory in {base}")
                else:
                    literal_reference(collector, script, base, check, resources, evidence)
    if any("$" in re.sub(r"^(?:\$HOME/|\$\{HOME\}/)", "", arg) or "%" in arg for arg in tokens[1:]):
        collector.problem(check, f"Dynamic command arguments require review in {base}")


def interpreter_dependency(text):
    if not text.startswith("#!"):
        return []
    try:
        words = shlex.split(text.splitlines()[0][2:].strip())
    except ValueError:
        return []
    if not words:
        return []
    if Path(words[0]).name == "env":
        words = words[1:]
        while words:
            word = words.pop(0)
            if word in {"-S", "--split-string"}:
                if not words:
                    return []
                try:
                    words[:1] = shlex.split(words[0])
                except ValueError:
                    return []
            elif word.startswith("--split-string="):
                try:
                    words[:0] = shlex.split(word.split("=", 1)[1])
                except ValueError:
                    return []
            elif word in {"-u", "--unset", "-C", "--chdir"}:
                if not words:
                    return []
                words.pop(0)
            elif word in {"-", "-i", "--ignore-environment", "-v", "--debug", "--"}:
                continue
            elif word.startswith(("--unset=", "--chdir=")) or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", word):
                continue
            elif word.startswith("-"):
                # Unknown options may consume arguments: guessing could report
                # an option value as the interpreter dependency.
                return []
            else:
                words = [word]
                break
    if not words or any(token in words[0] for token in ("$", "`", "\n")):
        return []
    name = Path(words[0]).name
    return [name] if name else []
