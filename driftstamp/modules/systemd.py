"""Local systemd unit inventory; no daemon or service commands are run."""
import re

from driftstamp.model import ModuleSpec
from .common import Collector, command_references, make_finding, scoped


def unit_directives(text, path, collector):
    """Read simple section assignments; leave continued directives unresolved."""
    section = ""
    continued = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if continued or line.endswith("\\"):
            collector.problem("systemd.references", f"Multiline directive requires review in {path}")
            continued = line.endswith("\\")
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif "=" in line:
            key, value = line.split("=", 1)
            yield section, key.strip(), value.strip()


class SystemdModule:
    spec = ModuleSpec("systemd", "Local systemd units and drop-ins", ("linux",), (
        ("systemd.user", "Inspect ~/.config/systemd/user"),
        ("systemd.system", "Inspect /etc/systemd/system"),
        ("systemd.references", "Link local timers, services and literal Exec commands"),
    ))

    def scan(self, ctx):
        c = Collector(ctx, self.spec)
        scoped(ctx, c, "systemd.user", "systemd.system")
        roots = []
        if ctx.scope != "system":
            roots.append((ctx.home / ".config/systemd/user", "systemd.user"))
        if ctx.scope != "user":
            roots.append((ctx.system("etc/systemd/system"), "systemd.system"))
        for root, check in roots:
            paths = [path for path in c.candidates(root, check, True) if path.suffix in {".service", ".timer", ".socket", ".path", ".target", ".mount", ".automount", ".conf"} and not any(part.endswith((".wants", ".requires")) for part in path.relative_to(root).parts[:-1])]
            texts = {}
            for path in paths:
                text = c.read(path, check)
                if text is not None:
                    texts[path] = text
            directives = {path: list(unit_directives(text, path, c)) for path, text in texts.items()}
            dropin_directories = {path.parent for path in texts
                                  if path.suffix == ".conf" and path.parent.name.endswith(".d")}
            for directory in sorted(dropin_directories):
                if directory.parent / directory.name[:-2] not in texts:
                    c.problem("systemd.references", f"Drop-in base is outside local inventory; inheritance requires review: {directory}")

            def with_dropins(unit_path):
                for directory in sorted(dropin_directories):
                    if directory == unit_path.parent / (unit_path.name + ".d"):
                        continue
                    prefix = directory.name.removesuffix(unit_path.suffix + ".d")
                    if directory.name == unit_path.suffix.lstrip(".") + ".d" or (prefix.endswith(("-", "@")) and unit_path.stem.startswith(prefix)):
                        c.problem("systemd.references", f"Inherited drop-in requires review: {directory}")
                return [unit_path] + [candidate for candidate in sorted(texts)
                                      if candidate.parent == unit_path.parent / (unit_path.name + ".d")]

            for path, text in texts.items():
                resources = [c.resource(path, check)]
                evidence = [f"Local unit or drop-in: {ctx.uri(path)}"]
                dependencies, warnings = [], ["Local placement does not prove a user edit; package ownership and active unit state are not checked."]
                related = with_dropins(path)
                if path.suffix == ".timer":
                    unit = path.with_suffix(".service").name
                    for timer_path in related:
                        for section, key, value in directives[timer_path]:
                            if section == "Timer" and key == "Unit":
                                unit = value
                    if not re.fullmatch(r"[A-Za-z0-9:_.@-]+\.(?:service|socket|target|path|mount|automount|slice|scope)", unit):
                        c.problem("systemd.references", f"Dynamic or unsupported timer unit requires review: {path}")
                    elif root / unit in texts:
                        related.extend(with_dropins(root / unit))
                        evidence.append(f"Timer activates local unit: {ctx.uri(root / unit)}")
                    else:
                        warnings.append(f"Timer target {unit} is not a discovered local unit; it may be supplied by a package.")
                        c.problem("systemd.references", f"Timer target is outside local inventory: {unit}")
                commands = {}
                for unit_path in related:
                    if unit_path != path:
                        resources.append(c.resource(unit_path, check))
                        if unit_path.suffix == ".conf":
                            evidence.append(f"Associated drop-in: {ctx.uri(unit_path)}")
                    for section, key, value in directives[unit_path]:
                        if section not in {"Service", "Socket", "Mount", "Swap"}:
                            continue
                        if key in {"ExecStart", "ExecStartPre", "ExecStartPost", "ExecStop", "ExecStopPost", "ExecReload"}:
                            if not value:
                                commands[key] = []
                            else:
                                commands.setdefault(key, []).append((value, unit_path))
                        elif key in {"Environment", "EnvironmentFile", "WorkingDirectory", "RootDirectory", "RootImage", "ExecSearchPath"}:
                            warnings.append(f"Related setting requires migration review: {key}")
                            c.problem("systemd.references", f"Additional execution context requires review in {unit_path}")
                for entries in commands.values():
                    for command, unit_path in entries:
                        command_references(c, command, unit_path.parent, "systemd.references", resources, evidence, dependencies)
                if any(resources):
                    c.result.findings.append(make_finding(ctx, "systemd", path, f"Systemd: {path.name}", "Local unit definition and statically linked execution resources.", resources, evidence, dependencies, warnings, confidence=90 if len(resources) > 1 else 75))
        return c.finish()
