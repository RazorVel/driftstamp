"""Readable cron file inventory, without invoking crontab or a scheduler."""
import re

from driftstamp.model import ModuleSpec
from .common import Collector, command_references, interpreter_dependency, make_finding, scoped


class CronModule:
    spec = ModuleSpec("cron", "Cron jobs and periodic scripts", ("linux",), (
        ("cron.user", "Inspect the selected user's conventional cron spool files"),
        ("cron.system", "Inspect /etc/crontab, cron.d and periodic script directories"),
        ("cron.references", "Inspect literal commands in conventional crontab entries"),
    ))

    def scan(self, ctx):
        c = Collector(ctx, self.spec)
        scoped(ctx, c, "cron.user", "cron.system")
        paths = []
        if ctx.scope != "system":
            paths += [(ctx.system(name) / ctx.home.name, "cron.user", False, False) for name in ("var/spool/cron/crontabs", "var/spool/cron")]
        if ctx.scope != "user":
            paths.append((ctx.system("etc/crontab"), "cron.system", True, False))
            paths += [(p, "cron.system", True, False) for p in c.candidates(ctx.system("etc/cron.d"), "cron.system", True)]
            for directory in ("cron.hourly", "cron.daily", "cron.weekly", "cron.monthly"):
                paths += [(p, "cron.system", False, True) for p in c.candidates(ctx.system("etc") / directory, "cron.system", True)]
        for path, check, user_column, script in paths:
            if not c.candidates(path, check):
                continue
            text = c.read(path, check)
            if text is None:
                continue
            resources = [c.resource(path, check)]
            evidence, dependencies = [], []
            if script:
                dependencies += interpreter_dependency(text)
            else:
                for line in text.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=", line):
                        continue
                    fields = (1 if line.startswith("@") else 5) + int(user_column)
                    pieces = line.split(None, fields)
                    if len(pieces) != fields + 1:
                        c.problem("cron.references", f"Unrecognized cron entry in {path}")
                        continue
                    if line.startswith("@") and pieces[0] not in {"@reboot", "@yearly", "@annually", "@monthly", "@weekly", "@daily", "@midnight", "@hourly"}:
                        c.problem("cron.references", f"Unrecognized cron schedule in {path}")
                        continue
                    command_references(c, pieces[-1], ctx.home if not user_column else path.parent, "cron.references", resources, evidence, dependencies)
            if any(resources):
                c.result.findings.append(make_finding(ctx, "cron", path, f"Cron: {path.name}", "Scheduled job configuration candidate.", resources, evidence or None, dependencies, ["Scheduler state, anacron, and commands inside periodic script bodies are not interpreted.", "Cron spool username is inferred from the selected home directory name."]))
        return c.finish()
