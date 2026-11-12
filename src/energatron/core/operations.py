"""Operations (P7 spec §3, §6, §8): reminders, the cheaper-plan test, notification texts and the launchd plist. Pure.

Reminders are warnings computed from a plain `Facts` value that `services/status.py` gathers. They decide nothing
about results, so their thresholds are constants, not params (P7 spec §14 Q1). Notification texts hold plan names and
the REC ID only, never a figure (Q4).
"""

import plistlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final

from energatron.core.errors import ResultWarning, WarningCode
from energatron.core.models.recommend import Recommendation, RecommendedOption

DRILL_INTERVAL_DAYS: Final = 91  # plan §6: quarterly
OFFERS_STALE_DAYS: Final = 14  # two missed weekly checks

# Reminders


@dataclass(frozen=True)
class Facts:
    """What the reminders are computed from; dates are local (P7 spec §3.2)."""

    today: date
    landing_new: int
    landing_landed: int
    synced: bool
    code_stale: tuple[str, ...]
    has_clean_run: bool
    datasets_not_in_last_clean_run: int
    last_fetch: date | None
    recheck_by: date | None
    refs_backed_up: bool
    commits_not_backed_up: int | None
    last_drill: date | None
    tool_unpublished: bool | None = None


def days_since(today: date, then: date | None) -> int | None:
    return None if then is None else (today - then).days


def drill_due(today: date, last_drill: date | None) -> bool:
    age = days_since(today, last_drill)
    return age is None or age > DRILL_INTERVAL_DAYS


def offers_stale(today: date, last_fetch: date | None) -> bool:
    age = days_since(today, last_fetch)
    return age is None or age > OFFERS_STALE_DAYS


def run_behind(facts: Facts) -> bool:
    return (
        not facts.synced
        or bool(facts.code_stale)
        or not facts.has_clean_run
        or facts.datasets_not_in_last_clean_run > 0
    )


def reminders(facts: Facts) -> list[ResultWarning]:
    """The reminders that apply, in P7 spec §3.3's order. Details hold counts and dates only."""
    found: list[ResultWarning] = []
    if facts.landing_new:
        found.append(
            _warn(
                WarningCode.W_LANDING_NEW,
                "The inbox holds files not landed yet: run `energatron land data/landing/`.",
                files=facts.landing_new,
            )
        )
    if run_behind(facts):
        first = "" if facts.synced else "`energatron pipeline sync`, then "
        found.append(
            _warn(
                WarningCode.W_RUN_BEHIND,
                f"The last clean run is behind the workspace: run {first}`energatron run`.",
                synced=facts.synced,
                stale_stages=len(facts.code_stale),
                datasets=facts.datasets_not_in_last_clean_run,
                clean_run=facts.has_clean_run,
            )
        )
    if offers_stale(facts.today, facts.last_fetch):
        found.append(
            _warn(
                WarningCode.W_OFFERS_STALE,
                "Offers haven't been fetched recently: run `energatron fetch-offers` (or schedule `energatron watch`).",
                days_since=days_since(facts.today, facts.last_fetch),
            )
        )
    if facts.recheck_by is not None and facts.recheck_by <= facts.today:
        found.append(
            _warn(
                WarningCode.W_RECHECK_DUE,
                "The recommendation's recheck date has come: refresh offers and run.",
                recheck_by=facts.recheck_by.isoformat(),
            )
        )
    if not facts.refs_backed_up:
        found.append(
            _warn(
                WarningCode.W_BACKUP_PENDING,
                "A commit isn't backed up yet: the owner runs `icloud_backup.py backup`.",
                commits_not_backed_up=facts.commits_not_backed_up,
            )
        )
    if drill_due(facts.today, facts.last_drill):
        found.append(
            _warn(
                WarningCode.W_DRILL_DUE,
                "The quarterly restore drill is due: the owner runs `icloud_backup.py backup --drill`.",
                days_since=days_since(facts.today, facts.last_drill),
            )
        )
    if facts.tool_unpublished:
        found.append(
            _warn(
                WarningCode.W_TOOL_UNPUBLISHED,
                "The last run's tool commit isn't pushed: the owner pushes the tool repo before a restore drill.",
            )
        )
    if facts.landing_landed:
        found.append(
            _warn(
                WarningCode.W_LANDING_LANDED,
                "The inbox holds files already landed: `energatron landing clear` may delete them.",
                files=facts.landing_landed,
            )
        )
    return found


def _warn(code: WarningCode, message: str, **details: int | str | bool | None) -> ResultWarning:
    return ResultWarning(code=code, message=message, details=dict(details))


# The cheaper-plan test and notifications (P7 spec §6)

_REMINDER_NAMES: Final = {
    WarningCode.W_LANDING_NEW: "new files in the inbox",
    WarningCode.W_RECHECK_DUE: "recommendation recheck due",
    WarningCode.W_BACKUP_PENDING: "backup pending",
    WarningCode.W_DRILL_DUE: "restore drill due",
}


@dataclass(frozen=True)
class Notification:
    title: str
    message: str


def plan_ids(option: RecommendedOption) -> tuple[str | None, str | None]:
    return (
        option.electricity.plan_id if option.electricity else None,
        option.gas.plan_id if option.gas else None,
    )


def cheaper_plan(before: Recommendation | None, after: Recommendation | None) -> bool:
    """Whether `after` recommends switching to plans `before` didn't top (P7 spec §6 step 5, §14 Q3)."""
    if after is None or after.action != "switch" or not after.options:
        return False
    if before is None or before.action != "switch" or not before.options:
        return True
    return plan_ids(before.options[0]) != plan_ids(after.options[0])


def option_names(option: RecommendedOption) -> dict[str, str | None]:
    """`{"electricity": "<brand> <display_name>" | None, "gas": …}`."""
    return {
        "electricity": f"{option.electricity.brand} {option.electricity.display_name}" if option.electricity else None,
        "gas": f"{option.gas.brand} {option.gas.display_name}" if option.gas else None,
    }


def notifications(
    *,
    after: Recommendation | None,
    cheaper: bool,
    failed: Sequence[tuple[str, str]],
    reminder_codes: Sequence[str],
) -> list[Notification]:
    """At most three notifications: a cheaper plan, a failure, the reminders worth a nudge (P7 spec §6 step 6).

    `failed` holds `(step, error code)` pairs, such as `("fetch", "E_NETWORK")`.
    """
    found: list[Notification] = []
    if cheaper and after is not None and after.options:
        names = option_names(after.options[0])
        parts = [f"{name} ({fuel})" for fuel, name in names.items() if name is not None]
        found.append(
            Notification(
                "energatron: cheaper plan",
                f"{' + '.join(parts)} tops {after.recommendation_id or 'the recommendation'}; "
                "see the recommendation page.",
            )
        )
    if failed:
        steps = ", ".join(f"{step} failed with {code}" for step, code in failed)
        found.append(
            Notification("energatron: weekly check failed", f"{steps}; run `energatron status` in the workspace.")
        )
    named = [_REMINDER_NAMES[WarningCode(code)] for code in reminder_codes if code in _REMINDER_NAMES]
    if named:
        found.append(Notification("energatron: reminders", ", ".join(named) + "."))
    return found


# The launchd agent (P7 spec §8)

LAUNCHD_PATH_DIRS: Final = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")
WATCH_LOG: Final = ".energatron/logs/watch.log"


@dataclass(frozen=True)
class Schedule:
    weekday: int  # 0 Sunday … 6 Saturday, as launchd counts
    hour: int
    minute: int

    def __post_init__(self) -> None:
        if not (0 <= self.weekday <= 6 and 0 <= self.hour <= 23 and 0 <= self.minute <= 59):
            raise ValueError("weekday 0-6, hour 0-23, minute 0-59")


def launchd_label(root_sha256: str) -> str:
    """One agent per workspace; the label holds a hash of the root's path, never the path."""
    return f"org.energatron.watch.{root_sha256[:8]}"


def launchd_plist(*, label: str, uv: str, workspace: str, schedule: Schedule) -> bytes:
    """The agent that runs `uv run --frozen energatron watch --notify --json` in `workspace` weekly."""
    uv_dir = uv.rsplit("/", 1)[0] if "/" in uv else ""
    path = ":".join(dict.fromkeys(item for item in (uv_dir, *LAUNCHD_PATH_DIRS) if item))
    document = {
        "Label": label,
        "ProgramArguments": [uv, "run", "--frozen", "energatron", "watch", "--notify", "--json"],
        "WorkingDirectory": workspace,
        "EnvironmentVariables": {"PATH": path},
        "StartCalendarInterval": {"Weekday": schedule.weekday, "Hour": schedule.hour, "Minute": schedule.minute},
        "StandardOutPath": f"{workspace}/{WATCH_LOG}",
        "StandardErrorPath": f"{workspace}/{WATCH_LOG}",
        "RunAtLoad": False,
        "ProcessType": "Background",
    }
    return plistlib.dumps(document, sort_keys=True)


def read_schedule(plist: bytes) -> Schedule | None:
    """The schedule a plist holds, or None when it isn't one of ours."""
    try:
        document = plistlib.loads(plist)
        interval = document["StartCalendarInterval"]
        return Schedule(int(interval["Weekday"]), int(interval["Hour"]), int(interval["Minute"]))
    except Exception:
        return None
