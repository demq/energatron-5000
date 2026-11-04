"""The usage report page (P3 spec §9, §13.1 `test_usage_report`). Every value is made up."""

import os
import re
from datetime import date
from pathlib import Path
from typing import Final

import pandas as pd
import pytest

from energatron.core import charts
from energatron.core.models.usage import SuffixSummary, WindowSetUsed
from energatron.core.usage_report import TITLE, UsageMarts, render
from tests.fixtures.usage_marts import SECOND_SET_FROM, SECOND_WINDOWS, Marts, build_marts, report_marts

GOLDEN: Final = Path(__file__).parents[2] / "fixtures" / "golden" / "usage_report.html"
HOSTILE: Final = '<b>"&</svg>'
SECTIONS: Final = (
    "Headline",
    "Monthly usage",
    "Average daily use by month and season",
    "Year on year",
    "Hourly profile",
    "Day by hour heatmap",
    "Time-of-use split",
    "Base load, peak demand and load factor",
    "Data quality",
)


def page(marts: Marts | None = None, **kwargs: object) -> str:
    chosen = marts or report_marts(**kwargs)  # type: ignore[arg-type]
    return render(UsageMarts(tables=chosen.tables, summary=chosen.summary))


def with_second_suffix(marts: Marts, suffix: str = "E2") -> Marts:
    tables = {}
    for name, frame in marts.tables.items():
        copy = frame.copy()
        copy["suffix"] = suffix
        tables[name] = pd.concat([frame, copy], ignore_index=True)
    first = next(iter(marts.summary.suffixes.values()))
    summary = marts.summary.model_copy(update={"suffixes": {"E1": first, suffix: first}})
    return Marts(tables, summary)


def relabel(marts: Marts, *, labels: dict[str, str] | None = None, suffix: str | None = None) -> Marts:
    """Rename TOU labels and the register in every table and in the summary."""
    mapping = labels or {}
    tables = {}
    for name, frame in marts.tables.items():
        copy = frame.copy()
        if "label" in copy:
            copy["label"] = copy["label"].replace(mapping)
        if suffix is not None:
            copy["suffix"] = suffix
        tables[name] = copy
    summary = marts.summary
    updates: dict[str, object] = {}
    only = next(iter(summary.suffixes.values()))
    shares = {mapping.get(k, k): v for k, v in only.tou_shares.items()}
    key = suffix if suffix is not None else next(iter(summary.suffixes))
    updates["suffixes"] = {key: only.model_copy(update={"tou_shares": shares})}
    updates["window_sets_used"] = [
        WindowSetUsed(effective_from=s.effective_from, windows={mapping.get(k, k): v for k, v in s.windows.items()})
        for s in summary.window_sets_used
    ]
    return Marts(tables, summary.model_copy(update=updates))


class TestStructure:
    def test_title_is_fixed_text(self) -> None:
        html = page()
        assert f"<title>{TITLE}</title>" in html
        assert TITLE == "Household electricity usage"
        assert f"<h1>{TITLE}</h1>" in html

    def test_a_section_per_register_with_every_item(self) -> None:
        html = page(with_second_suffix(report_marts()))
        assert html.count('<section class="register"') == 2
        for heading in SECTIONS:
            assert html.count(f"<h3>{heading}</h3>") == 2, heading
        assert "<h2>Register E1</h2>" in html
        assert "<h2>Register E2</h2>" in html

    def test_every_chart_is_an_accessible_img(self) -> None:
        html = page()
        svgs = html.count("<svg ")
        assert svgs >= 14
        assert html.count('role="img"') == svgs
        assert html.count("<desc ") == svgs
        assert html.count("</svg>") == svgs
        for ident in re.findall(r'aria-labelledby="([^"]+)"', html):
            for part in ident.split():
                assert html.count(f'id="{part}"') == 1, part

    def test_ids_are_unique(self) -> None:
        html = page(with_second_suffix(report_marts()))
        ids = re.findall(r' id="([^"]+)"', html)
        assert len(ids) == len(set(ids))

    def test_a_chart_uses_at_most_four_categorical_tones(self) -> None:
        html = page()
        for svg in re.findall(r"<svg .*?</svg>", html, flags=re.S):
            tones = set(re.findall(r'class="[^"]*\b(c\d)\b', svg))
            assert len(tones) <= 4

    def test_one_colour_scheme_block_and_no_script(self) -> None:
        html = page()
        assert html.count("prefers-color-scheme") == 1
        assert html.startswith("<!DOCTYPE html>")
        assert '<html lang="en">' in html

    def test_no_register_means_a_plain_note(self) -> None:
        marts = report_marts()
        empty = marts.summary.model_copy(update={"suffixes": {}})
        html = page(Marts({k: v.iloc[0:0] for k, v in marts.tables.items()}, empty))
        assert "No consumption register" in html
        assert 'class="register"' not in html


class TestNoNetworkAndEscaping:
    def test_no_network_reference_script_or_style_import(self) -> None:
        html = page(with_second_suffix(report_marts()))
        for banned in (
            "http:",
            "https:",
            "<script",
            "<link",
            "src=",
            "@import",
            "url(",
            "javascript:",
            "<img",
            "<iframe",
        ):
            assert banned not in html, banned

    def test_hostile_labels_are_escaped_everywhere(self) -> None:
        marts = relabel(report_marts(), labels={"Peak": HOSTILE, "Off Peak": "Q&A"}, suffix=HOSTILE)
        html = page(marts)
        assert HOSTILE not in html
        assert "&lt;b&gt;&quot;&amp;&lt;/svg&gt;" in html
        assert "Q&amp;A" in html
        assert html.count("<svg ") == html.count("</svg>")
        assert html.count("<section") == html.count("</section>")

    def test_hostile_text_never_reaches_the_style_or_an_attribute_name(self) -> None:
        marts = relabel(report_marts(), labels={"Peak": HOSTILE}, suffix=HOSTILE)
        html = page(marts)
        style = re.search(r"<style>(.*?)</style>", html, flags=re.S)
        assert style is not None
        assert "<b>" not in style.group(1)
        assert HOSTILE not in " ".join(re.findall(r"<[^>]+>", html))

    def test_no_identifier_shaped_text(self) -> None:
        html = page()
        assert not re.search(r"\b\d{10,}\b", html)  # no long numbers: NMI, serial or account shapes


class TestContent:
    def test_headline_reads_the_summary(self) -> None:
        marts = report_marts()
        only = next(iter(marts.summary.suffixes.values()))
        html = page(marts)
        assert only.window_start is not None
        assert only.window_end is not None
        assert only.window_start in html
        assert only.window_end in html
        assert f"{only.window_complete_days} of {only.window_days} days complete" in html
        for label in only.tou_shares:
            assert f">{label}<" in html

    def test_monthly_chart_names_the_hatched_months(self) -> None:
        html = page()
        desc = re.search(r'<desc id="d-monthly-0">(.*?)</desc>', html, flags=re.S)
        assert desc is not None
        assert "Partial months" in desc.group(1)
        assert "2024-01" in desc.group(1)  # starts mid-month
        assert "2024-02" in desc.group(1)  # one incomplete day
        assert "2024-06" not in desc.group(1)
        svg = re.search(r'<svg [^>]*aria-labelledby="t-monthly-0 d-monthly-0".*?</svg>', html, flags=re.S)
        assert svg is not None
        assert svg.group(0).count('class="hatch') >= 3  # 2024-01, 2024-02 and 2025-04

    def test_season_groups_label_the_average_chart(self) -> None:
        html = page()
        chart = re.search(r'<svg [^>]*aria-labelledby="t-average-0 d-average-0".*?</svg>', html, flags=re.S)
        assert chart is not None
        for season in ("DJF", "MAM", "JJA", "SON"):
            assert f">{season}<" in chart.group(0)

    def test_seasonal_table_follows(self) -> None:
        html = page()
        assert "<caption>Average daily use by season</caption>" in html

    def test_year_on_year_shows_the_change_and_only_matched_months(self) -> None:
        marts = report_marts()
        html = page(marts)
        chart = re.search(r'<svg [^>]*aria-labelledby="t-yoy-0 d-yoy-0".*?</svg>', html, flags=re.S)
        assert chart is not None
        assert "2025-02" in chart.group(0)
        assert "2025-03" in chart.group(0)
        assert "2025-04" not in chart.group(0)
        assert "2024-02" in chart.group(0)  # the prior month is named in the tooltip
        assert "+0.1%" in chart.group(0)

    def test_no_prior_year_says_so(self) -> None:
        marts = build_marts()  # 14 months: no month has a covered prior year
        html = page(marts)
        assert "No month has a prior year" in html

    def test_hourly_profile_has_a_panel_per_season_and_daytype(self) -> None:
        marts = report_marts()
        html = page(marts)
        combos = marts.tables["hourly_profile"][["season", "daytype"]].drop_duplicates()
        panels = re.findall(r'aria-labelledby="t-hourly-0-\d+ d-hourly-0-\d+"', html)
        assert len(panels) == len(combos)
        assert "DJF · weekday" in html
        assert "p10 to p90" in html

    def test_few_days_in_a_panel_is_said(self) -> None:
        marts = report_marts()
        frame = marts.tables["hourly_profile"].copy()
        weak = (frame["season"] == "MAM") & (frame["daytype"] == "weekend")
        frame.loc[weak, "cells"] = 3
        tables = {**marts.tables, "hourly_profile": frame}
        html = page(Marts(tables, marts.summary))
        assert "Only 3 days" in html

    def test_heatmap_leaves_the_missing_hour_blank_and_says_why(self) -> None:
        marts = report_marts()
        cells = marts.tables["heatmap"]
        filled = int(cells["kwh"].notna().sum())
        html = page(marts)
        chart = re.search(r'<svg [^>]*aria-labelledby="t-heatmap-0 d-heatmap-0".*?</svg>', html, flags=re.S)
        assert chart is not None
        assert (
            len(re.findall(r'<rect x="[\d.]+" y="[\d.]+" width="[\d.]+" height="[\d.]+" class="q\d">', chart.group(0)))
            == filled
        )
        assert filled < len(cells)
        assert "daylight-saving" in chart.group(0)

    def test_tou_split_lists_the_window_history(self) -> None:
        two = page(report_marts(second_set=True))
        assert "effective from" in two
        assert SECOND_SET_FROM.isoformat() in two
        for label, windows in SECOND_WINDOWS.items():
            assert label in two
            assert windows[0] in two
        assert "may have changed" not in two

    def test_one_window_set_adds_the_caution(self) -> None:
        one = page(report_marts(second_set=False))
        assert "may have changed" in one
        assert "effective from" not in one

    def test_mixed_window_months_carry_a_marker_and_a_note(self) -> None:
        html = page(report_marts(second_set=True))
        chart = re.search(r'<svg [^>]*aria-labelledby="t-tou-0 d-tou-0".*?</svg>', html, flags=re.S)
        assert chart is not None
        assert "2024-09*" in chart.group(0)
        assert "2024-08*" not in chart.group(0)
        assert "* The month spans two window sets" in html

    def test_no_marker_without_mixed_months(self) -> None:
        html = page(report_marts(second_set=False))
        assert "* The month spans" not in html

    def test_tou_tooltips_carry_the_share(self) -> None:
        html = page()
        assert re.search(r"<title>2024-08\nOff Peak: [^\n]*\nPeak: \d+\.\d% · \d+\.\d kWh\n", html)

    def test_peak_demand_tooltip_names_the_time(self) -> None:
        marts = report_marts()
        monthly = marts.tables["monthly"]
        at = monthly.loc[monthly["month"] == "2025-01", "peak_demand_at"].iloc[0]
        assert f"at {pd.Timestamp(at).strftime('%Y-%m-%d %H:%M')}" in page(marts)

    def test_quality_classes_are_named_in_a_legend(self) -> None:
        html = page()
        for name in ("actual", "estimated", "substituted", "other"):
            assert f">{name}<" in html

    def test_a_data_table_twin_for_the_monthly_charts(self) -> None:
        html = page()
        assert html.count("<details>") >= 6
        assert "<summary>Table</summary>" in html

    def test_dst_days_are_reported_in_the_quality_section(self) -> None:
        html = page()
        assert "1 short and 2 long local days" in html

    def test_nan_values_read_as_a_dash(self) -> None:
        marts = report_marts()
        monthly = marts.tables["monthly"].copy()
        monthly.loc[0, ["base_load_kw", "load_factor"]] = float("nan")
        html = page(Marts({**marts.tables, "monthly": monthly}, marts.summary))
        assert "nan" not in html.lower().replace("financial", "")
        assert charts.DASH in html

    def test_summary_without_a_window_still_renders(self) -> None:
        marts = report_marts()
        only = next(iter(marts.summary.suffixes.values()))
        bare = SuffixSummary(
            first_date=date(2024, 1, 10),
            last_date=date(2025, 4, 20),
            covered_months=0,
            window_start=None,
            window_end=None,
            window_days=0,
            window_complete_days=0,
            window_coverage=None,
            avg_daily_kwh=None,
            base_load_kw=None,
            peak_demand_kw=None,
            load_factor=None,
            tou_shares={},
        )
        assert only.window_start is not None
        html = page(Marts(marts.tables, marts.summary.model_copy(update={"suffixes": {"E1": bare}})))
        assert "No reference window" in html


class TestDeterminism:
    def test_two_renders_are_byte_identical(self) -> None:
        marts = report_marts()
        first = render(UsageMarts(marts.tables, marts.summary))
        second = render(UsageMarts(dict(reversed(list(marts.tables.items()))), marts.summary))
        assert first == second

    def test_row_order_of_the_marts_does_not_matter(self) -> None:
        marts = report_marts()
        shuffled = {
            name: frame.sample(frac=1.0, random_state=7).reset_index(drop=True) for name, frame in marts.tables.items()
        }
        assert page(Marts(shuffled, marts.summary)) == page(marts)

    def test_no_clock_in_the_page(self) -> None:
        html = page()
        assert not re.search(r"generated|rendered at|\d{2}:\d{2}:\d{2}", html)

    def test_matches_the_golden_page(self) -> None:
        html = page()
        if os.environ.get("UPDATE_GOLDEN") == "1":
            GOLDEN.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN.write_text(html, encoding="utf-8")
        assert GOLDEN.exists(), "run once with UPDATE_GOLDEN=1 to write it"
        assert html == GOLDEN.read_text(encoding="utf-8")


class TestSizeBudget:
    def test_three_years_of_cells_stay_under_four_megabytes(self) -> None:
        marts = report_marts()
        full = page(marts)
        bare = page(Marts({**marts.tables, "heatmap": marts.tables["heatmap"].iloc[0:0]}, marts.summary))
        cells = int(marts.tables["heatmap"]["kwh"].notna().sum())
        per_cell = (len(full.encode()) - len(bare.encode())) / cells
        projected = len(bare.encode()) + per_cell * 3 * 366 * 24
        assert projected < 4 * 1024 * 1024, f"{per_cell:.0f} bytes a cell, {projected / 1e6:.2f} MB projected"

    def test_the_golden_page_is_small(self) -> None:
        assert len(page().encode()) < 400_000


@pytest.mark.parametrize("second_set", [True, False])
def test_renders_both_window_histories(second_set: bool) -> None:
    assert page(report_marts(second_set=second_set)).endswith("</html>\n")
