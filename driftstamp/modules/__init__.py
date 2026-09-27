"""Bundled, independently replaceable discovery modules."""
from .cron import CronModule
from .i3 import I3Module
from .scripts import ScriptsModule
from .shell import ShellModule
from .systemd import SystemdModule


def builtin_modules():
    return [I3Module(), ShellModule(), ScriptsModule(), SystemdModule(), CronModule()]
