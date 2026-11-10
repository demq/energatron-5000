"""`schedule_result` v1 (P7 spec §8): what `energatron schedule` reports.

`schedule` configures this machine, not the workspace, so it has no service function (P7 spec §14 Q5); its result kind
lives here because every CLI result is a `ServiceResult`.
"""

from typing import ClassVar, Literal

from energatron.services.context import ServiceResult


class ScheduleResult(ServiceResult):
    """`schedule_result` v1 (P7 spec §8). Paths show the home folder as `~`; the log path is workspace-relative."""

    KIND: ClassVar[str] = "schedule_result"
    SCHEMA_VERSION: ClassVar[int] = 1
    kind: Literal["schedule_result"] = "schedule_result"
    schema_version: Literal[1] = 1

    action: Literal["install", "remove", "status"]
    dry_run: bool
    label: str
    plist: str
    installed: bool
    loaded: bool
    weekday: int | None
    hour: int | None
    minute: int | None
    log: str
