"""An extension API example, not an implementation of RPM inspection."""

from driftstamp.context import ScanContext
from driftstamp.model import Check, ModuleResult, ModuleSpec


class RedHatExample:
    """Make an unimplemented inspection capability visible in the report."""

    spec = ModuleSpec(
        id="example-rhel-config",
        title="Red Hat RPM configuration (example; unsupported)",
        platforms=("linux",),
        checks=(
            (
                "example-rhel-config.rpm-config",
                "Compare RPM-managed configuration with a verified package baseline",
            ),
        ),
        api_version=1,
    )

    def scan(self, ctx: ScanContext) -> ModuleResult:
        if ctx.scope == "user":
            return ModuleResult(
                checks=[
                    Check(
                        id=self.spec.checks[0][0],
                        status="excluded",
                        detail="System package configuration is outside user scope.",
                    )
                ]
            )

        return ModuleResult(
            checks=[
                Check(
                    id=self.spec.checks[0][0],
                    status="unsupported",
                    detail=(
                        "This extension demonstrates API v1 registration only. "
                        "RPM metadata collection and package-baseline comparison "
                        "are not implemented; no files were inspected."
                    ),
                )
            ],
            warnings=[
                "The Red Hat example does not provide RPM discovery or migration support."
            ],
        )
