"""`core/operations.py` (P7 spec §3, §6, §8): reminders at their boundaries, the cheaper-plan test, notification
texts and the launchd plist. Recommendations are the golden synthetic `recommendation` v1 document, varied."""

import json
import plistlib
import re
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from energatron.core.errors import WarningCode
from energatron.core.models.recommend import Recommendation
from energatron.core.operations import (
    DRILL_INTERVAL_DAYS,
    OFFERS_STALE_DAYS,
    Facts,
    Schedule,
    cheaper_plan,
    launchd_label,
    launchd_plist,
    notifications,
    read_schedule,
    reminders,
)

TODAY = date(2026, 11, 10)
GOLDEN = Path(__file__).resolve().parents[2] / "fixtures" / "schemas" / "recommendation" / "v1.json"

QUIET = Facts(
    today=TODAY,
    landing_new=0,
    landing_landed=0,
    synced=True,
    code_stale=(),
    has_clean_run=True,
    datasets_not_in_last_clean_run=0,
    last_fetch=TODAY,
    recheck_by=TODAY + timedelta(days=1),
    refs_backed_up=True,
    commits_not_backed_up=0,
    last_drill=TODAY,
)


def codes(facts: Facts) -> list[WarningCode]:
    return [warning.code for warning in reminders(facts)]


def test_a_quiet_workspace_has_no_reminders() -> None:
    assert codes(QUIET) == []


def test_every_reminder_in_order() -> None:
    loud = Facts(
        today=TODAY,
        landing_new=2,
        landing_landed=1,
        synced=False,
        code_stale=("catalog_index",),
        has_clean_run=False,
        datasets_not_in_last_clean_run=3,
        last_fetch=None,
        recheck_by=TODAY,
        refs_backed_up=False,
        commits_not_backed_up=4,
        last_drill=None,
    )
    assert codes(loud) == [
        WarningCode.W_LANDING_NEW,
        WarningCode.W_RUN_BEHIND,
        WarningCode.W_OFFERS_STALE,
        WarningCode.W_RECHECK_DUE,
        WarningCode.W_BACKUP_PENDING,
        WarningCode.W_DRILL_DUE,
        WarningCode.W_LANDING_LANDED,
    ]
    behind = next(warning for warning in reminders(loud) if warning.code == WarningCode.W_RUN_BEHIND)
    assert "pipeline sync" in behind.message


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"synced": False}, WarningCode.W_RUN_BEHIND),
        ({"code_stale": ("x",)}, WarningCode.W_RUN_BEHIND),
        ({"has_clean_run": False}, WarningCode.W_RUN_BEHIND),
        ({"datasets_not_in_last_clean_run": 1}, WarningCode.W_RUN_BEHIND),
        ({"landing_new": 1}, WarningCode.W_LANDING_NEW),
        ({"landing_landed": 1}, WarningCode.W_LANDING_LANDED),
        ({"refs_backed_up": False}, WarningCode.W_BACKUP_PENDING),
    ],
)
def test_each_condition_alone_raises_its_reminder(change: dict[str, Any], code: WarningCode) -> None:
    assert codes(replace(QUIET, **change)) == [code]


def test_the_drill_is_due_after_its_interval_or_never() -> None:
    on_time = replace(QUIET, last_drill=TODAY - timedelta(days=DRILL_INTERVAL_DAYS))
    late = replace(QUIET, last_drill=TODAY - timedelta(days=DRILL_INTERVAL_DAYS + 1))
    assert codes(on_time) == []
    assert codes(late) == [WarningCode.W_DRILL_DUE]
    assert codes(replace(QUIET, last_drill=None)) == [WarningCode.W_DRILL_DUE]
    (warning,) = reminders(late)
    assert warning.details == {"days_since": DRILL_INTERVAL_DAYS + 1}


def test_offers_are_stale_after_two_weeks_or_never() -> None:
    assert codes(replace(QUIET, last_fetch=TODAY - timedelta(days=OFFERS_STALE_DAYS))) == []
    assert codes(replace(QUIET, last_fetch=TODAY - timedelta(days=OFFERS_STALE_DAYS + 1))) == [
        WarningCode.W_OFFERS_STALE
    ]
    assert codes(replace(QUIET, last_fetch=None)) == [WarningCode.W_OFFERS_STALE]


def test_the_recheck_is_due_on_its_date() -> None:
    assert codes(replace(QUIET, recheck_by=TODAY)) == [WarningCode.W_RECHECK_DUE]
    assert codes(replace(QUIET, recheck_by=None)) == []


# The cheaper-plan test


def recommendation(*, action: str = "switch", elec_plan: str = "SYN-E-1", gas: bool = False) -> Recommendation:
    document = json.loads(GOLDEN.read_text(encoding="utf-8"))
    document["action"] = action
    option = document["options"][0]
    option["electricity"]["plan_id"] = elec_plan
    option["electricity"]["offer_snapshot_id"] = f"{elec_plan}~20261101T000000Z~0123abcd"
    if gas:
        option["gas"] = option["electricity"] | {
            "plan_id": "SYN-G-1",
            "offer_snapshot_id": "SYN-G-1~20261101T000000Z~0123abcd",
            "display_name": "Gas Saver",
        }
    return Recommendation.from_document(document)


def test_a_first_switch_is_news() -> None:
    assert cheaper_plan(None, recommendation())
    assert cheaper_plan(recommendation(action="stay"), recommendation())


def test_the_same_plans_again_are_not_news_even_with_a_new_snapshot() -> None:
    before = recommendation()
    document = json.loads(recommendation().model_dump_json())
    document["options"][0]["electricity"]["offer_snapshot_id"] = "SYN-E-1~20261108T000000Z~89abcdef"
    after = Recommendation.from_document(document)
    assert not cheaper_plan(before, after)


def test_a_different_top_plan_is_news() -> None:
    assert cheaper_plan(recommendation(), recommendation(elec_plan="SYN-E-2"))
    assert cheaper_plan(recommendation(), recommendation(gas=True))


def test_no_switch_is_never_news() -> None:
    assert not cheaper_plan(None, recommendation(action="stay"))
    assert not cheaper_plan(recommendation(), None)


# Notifications


def test_notifications_name_plans_and_the_rec_but_hold_no_figure() -> None:
    after = recommendation(gas=True)
    found = notifications(
        after=after,
        cheaper=True,
        failed=[("run", "E_DIRTY")],
        reminder_codes=["W_RUN_BEHIND", "W_DRILL_DUE", "W_BACKUP_PENDING"],
    )
    assert [item.title for item in found] == [
        "energatron: cheaper plan",
        "energatron: weekly check failed",
        "energatron: reminders",
    ]
    assert "(electricity) + " in found[0].message and "(gas)" in found[0].message
    assert after.recommendation_id is not None and after.recommendation_id in found[0].message
    assert found[1].message.startswith("run failed with E_DIRTY")
    assert found[2].message == "restore drill due, backup pending."  # in the order given; W_RUN_BEHIND left out
    for item in found:
        text = item.message.replace(after.recommendation_id, "")
        assert not re.search(r"\d", text), item.message


def test_nothing_to_say_gives_no_notification() -> None:
    assert notifications(after=recommendation(), cheaper=False, failed=[], reminder_codes=["W_OFFERS_STALE"]) == []


# The launchd plist


def test_the_plist_runs_watch_weekly_with_a_path_that_reaches_uv() -> None:
    data = launchd_plist(
        label=launchd_label("ab" * 32),
        uv="/opt/homebrew/bin/uv",
        workspace="/scratch/ws",
        schedule=Schedule(weekday=1, hour=9, minute=0),
    )
    document = plistlib.loads(data)
    assert document["Label"] == "org.energatron.watch.abababab"
    assert document["ProgramArguments"] == [
        "/opt/homebrew/bin/uv", "run", "--frozen", "energatron", "watch", "--notify", "--json",
    ]  # fmt: skip
    assert document["WorkingDirectory"] == "/scratch/ws"
    assert document["EnvironmentVariables"]["PATH"].split(":") == [
        "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin",
    ]  # fmt: skip
    assert document["StartCalendarInterval"] == {"Weekday": 1, "Hour": 9, "Minute": 0}
    assert document["StandardOutPath"] == document["StandardErrorPath"] == "/scratch/ws/.energatron/logs/watch.log"
    assert (document["RunAtLoad"], document["ProcessType"]) == (False, "Background")
    assert read_schedule(data) == Schedule(1, 9, 0)


def test_an_unusual_uv_folder_comes_first_on_the_path() -> None:
    document = plistlib.loads(
        launchd_plist(label="l", uv="/home/x/.local/bin/uv", workspace="/w", schedule=Schedule(0, 0, 0))
    )
    assert document["EnvironmentVariables"]["PATH"].split(":")[0] == "/home/x/.local/bin"


def test_a_schedule_is_checked_and_a_foreign_plist_reads_as_none() -> None:
    with pytest.raises(ValueError):
        Schedule(7, 0, 0)
    with pytest.raises(ValueError):
        Schedule(0, 24, 0)
    assert read_schedule(b"not a plist") is None
    assert read_schedule(plistlib.dumps({"Label": "x"})) is None
