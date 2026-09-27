"""Text scripts only: do not turn local software binaries into migration files."""
from driftstamp.model import ModuleSpec
from .common import Collector, interpreter_dependency, make_finding, scoped


class ScriptsModule:
    spec = ModuleSpec("scripts", "Local text scripts", ("linux",), (
        ("scripts.user", "Inspect ~/bin, ~/.local/bin and ~/scripts for text scripts"),
        ("scripts.system", "Inspect /usr/local/bin and /usr/local/sbin for text scripts"),
        ("scripts.includes", "Inspect explicitly included files and directories for text scripts"),
    ))

    def scan(self, ctx):
        c = Collector(ctx, self.spec)
        scoped(ctx, c, "scripts.user", "scripts.system")
        roots = []
        if ctx.scope != "system":
            roots += [(ctx.home / name, "scripts.user") for name in ("bin", ".local/bin", "scripts")]
        if ctx.scope != "user":
            roots += [(ctx.system(name), "scripts.system") for name in ("usr/local/bin", "usr/local/sbin")]
        roots += [(path, "scripts.includes") for path in ctx.includes]
        visited = set()
        for root, check in roots:
            for path in c.candidates(root, check, root.is_dir()):
                if path in visited:
                    continue
                visited.add(path)
                if any(part in {".git", "node_modules", "__pycache__", ".venv", "venv", "build", "dist"} for part in path.relative_to(root if root.is_dir() else root.parent).parts):
                    continue
                # A filename extension or a shebang is evidence of a text script.
                # Binaries are expected here, so binary rejection isn't a failed check.
                if not ctx.allowed(path):
                    c.problem(check, f"Excluded script candidate: {path}", "excluded")
                    continue
                try:
                    text = ctx.read_text(path)
                except ValueError as exc:
                    if "binary" in str(exc).lower() or "utf" in str(exc).lower():
                        continue
                    c.problem(check, f"Cannot inspect script candidate {path}: {exc}")
                    continue
                except UnicodeError:
                    continue
                except OSError as exc:
                    c.problem(check, f"Cannot read script candidate {path}: {exc}", "blocked")
                    continue
                if not text.startswith("#!") and path.suffix not in {".sh", ".bash", ".zsh", ".py", ".pl", ".rb"}:
                    continue
                resource = c.resource(path, check)
                if resource:
                    c.result.findings.append(make_finding(ctx, "scripts", path, f"Local script: {path.name}", "Text script in a local customization location.", [resource], dependencies=interpreter_dependency(text), confidence=65, warnings=["Location and file content do not prove human authorship or current usage.", "Only the shebang interpreter is detected; dependencies inside script bodies require review."]))
        return c.finish()
