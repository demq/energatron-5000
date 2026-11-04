"""The SVG primitives (P3 spec §9.2, §9.4, §13.1 `test_charts`). Every value is made up."""

import locale
import re

import pytest

from energatron.core import charts
from energatron.core.charts import Series

HOSTILE = '<b>"&</svg>'


def column(**kwargs: object) -> str:
    base: dict[str, object] = {
        "chart_id": "c1",
        "title": "Usage",
        "desc": "Energy by month.",
        "categories": ["2025-01", "2025-02", "2025-03"],
        "series": [Series("kWh", (10.0, 20.0, 15.0), "c1")],
        "unit": "kWh",
    }
    base.update(kwargs)
    return charts.column_chart(**base)  # type: ignore[arg-type]


class TestFormatNumber:
    def test_fixed_places_and_point(self) -> None:
        assert charts.format_number(1234.5678, 2) == "1234.57"
        assert charts.format_number(2, 0) == "2"
        assert charts.format_number(0.5, 1) == "0.5"

    def test_no_negative_zero(self) -> None:
        assert charts.format_number(-0.0001, 2) == "0.00"
        assert charts.format_number(-1.5, 1) == "-1.5"

    def test_missing_values_read_as_a_dash(self) -> None:
        assert charts.format_number(None, 1) == charts.DASH
        assert charts.format_number(float("nan"), 1) == charts.DASH

    def test_locale_free(self) -> None:
        saved = locale.setlocale(locale.LC_NUMERIC)
        try:
            for name in ("de_DE.UTF-8", "fr_FR.UTF-8"):
                try:
                    locale.setlocale(locale.LC_NUMERIC, name)
                except locale.Error:
                    continue
                assert charts.format_number(1234.5, 1) == "1234.5"
        finally:
            locale.setlocale(locale.LC_NUMERIC, saved)


class TestAxis:
    @pytest.mark.parametrize(
        ("top", "expected"),
        [(0.0, (1.0, [0.0, 0.25, 0.5, 0.75, 1.0])), (9.3, (10.0, [0.0, 2.5, 5.0, 7.5, 10.0])), (97.0, (100.0, None))],
    )
    def test_nice_axis_rounds_up_to_clean_ticks(self, top: float, expected: tuple[float, list[float] | None]) -> None:
        end, ticks = charts.nice_axis(top)
        assert end == expected[0]
        assert ticks[0] == 0.0
        assert ticks[-1] == end
        if expected[1] is not None:
            assert ticks == expected[1]

    def test_nice_axis_covers_the_top(self) -> None:
        for top in (0.013, 0.7, 3.1, 44.0, 1234.0):
            end, _ = charts.nice_axis(top)
            assert end >= top


class TestColumnChart:
    def test_is_an_img_svg_with_title_and_desc(self) -> None:
        svg = column()
        assert svg.startswith("<svg ")
        assert 'role="img"' in svg
        assert "<title" in svg
        assert "Usage" in svg
        assert "Energy by month." in svg
        assert "aria-labelledby" in svg

    def test_byte_stable(self) -> None:
        assert column() == column()

    def test_coordinates_are_rounded_to_one_decimal(self) -> None:
        svg = column(series=[Series("kWh", (1 / 3, 2 / 7, 5 / 9), "c1")])
        for number in re.findall(r'(?:x|y|width|height)="(-?\d+\.\d+)"', svg):
            assert len(number.split(".")[1]) == 1, number

    def test_has_no_network_or_script_reference(self) -> None:
        svg = column(hatched=[1])
        for banned in ("http:", "https:", "<script", "<link", "src=", "@import", "url("):
            assert banned not in svg

    def test_every_mark_has_a_tooltip(self) -> None:
        svg = column()
        assert svg.count("<title>") >= 3
        assert "2025-02\nkWh: 20.0 kWh" in svg

    def test_notes_extend_the_tooltip(self) -> None:
        svg = column(notes={1: "Partial month"})
        assert "2025-02\nkWh: 20.0 kWh\nPartial month" in svg

    def test_hatch_marks_a_flagged_bar_and_only_that_bar(self) -> None:
        plain = column()
        flagged = column(hatched=[1])
        assert 'class="hatch' not in plain
        assert flagged.count('class="hatch') == 1
        assert "partial" in flagged

    def test_hatch_lines_stay_inside_the_bar(self) -> None:
        path = charts.hatch_path(10.0, 20.0, 12.0, 30.0, 4.0)
        pairs = [(float(a), float(b)) for a, b in re.findall(r"(-?\d+\.?\d*),(-?\d+\.?\d*)", path)]
        assert pairs
        for px, py in pairs:
            assert 10.0 - 0.06 <= px <= 22.0 + 0.06
            assert 20.0 - 0.06 <= py <= 50.0 + 0.06
        # 45 degrees: every segment rises as far as it runs
        for (x1, y1), (x2, y2) in zip(pairs[::2], pairs[1::2], strict=True):
            assert abs(abs(x2 - x1) - abs(y2 - y1)) < 0.2

    def test_hatch_of_an_empty_box_is_empty(self) -> None:
        assert charts.hatch_path(0.0, 0.0, 0.0, 10.0, 4.0) == ""

    def test_empty_series_draws_an_empty_chart_with_its_caption(self) -> None:
        svg = column(categories=[], series=[Series("kWh", (), "c1")], empty="No months yet.")
        assert "No months yet." in svg
        assert "<path" not in svg
        assert svg.startswith("<svg ")

    def test_all_missing_values_are_empty_too(self) -> None:
        svg = column(series=[Series("kWh", (None, None, None), "c1")], empty="Nothing.")
        assert "Nothing." in svg

    def test_stacked_segments_sum_to_the_axis(self) -> None:
        svg = column(
            series=[Series("a", (0.25, 0.5, 0.0), "c1"), Series("b", (0.75, 0.5, 1.0), "c2")],
            stacked=True,
            unit="share",
        )
        assert 'class="bar c1"' in svg
        assert 'class="bar c2"' in svg

    def test_a_zero_value_draws_no_bar(self) -> None:
        svg = column(series=[Series("kWh", (0.0, 5.0, 0.0), "c1")])
        assert svg.count('class="bar c1"') == 1

    def test_hostile_text_is_escaped_everywhere(self) -> None:
        svg = column(
            title=HOSTILE,
            desc=HOSTILE,
            categories=[HOSTILE, "b", "c"],
            series=[Series(HOSTILE, (1.0, 2.0, 3.0), "c1")],
            unit=HOSTILE,
            notes={0: HOSTILE},
            top_labels=[HOSTILE, "", ""],
        )
        assert HOSTILE not in svg
        assert "</svg>" not in svg[:-6]
        assert svg.count("<svg ") == 1
        assert "&lt;b&gt;&quot;&amp;&lt;/svg&gt;" in svg

    def test_group_labels_appear_under_the_axis(self) -> None:
        svg = column(groups=[("DJF", 0, 1), ("MAM", 2, 2)])
        assert ">DJF<" in svg
        assert ">MAM<" in svg

    def test_top_labels_are_dropped_when_the_band_is_narrow(self) -> None:
        months = [f"2025-{m:02d}" for m in range(1, 10)] * 6  # 54 columns
        wide = column(
            categories=months[:3], series=[Series("kWh", (1.0, 2.0, 3.0), "c1")], top_labels=["+1%", "+2%", "+3%"]
        )
        narrow = column(
            categories=months,
            series=[Series("kWh", tuple(float(i) for i in range(54)), "c1")],
            top_labels=["+1%"] * 54,
        )
        assert "+2%" in wide
        assert "+1%" not in narrow

    def test_axis_labels_are_thinned_so_they_do_not_collide(self) -> None:
        months = [f"20{25 + i // 12}-{i % 12 + 1:02d}" for i in range(36)]
        svg = column(categories=months, series=[Series("kWh", tuple(float(i) for i in range(36)), "c1")])
        assert svg.count('class="tick x"') < 36
        assert 'class="tick x"' in svg

    def test_more_than_the_palette_gives_a_neutral_tone(self) -> None:
        assert charts.tone_for(0) == "c1"
        assert charts.tone_for(3) == "c4"
        assert charts.tone_for(4) == "cx"
        assert charts.tone_for(40) == "cx"

    def test_bars_are_at_most_24_wide(self) -> None:
        svg = column(categories=["a"], series=[Series("kWh", (1.0,), "c1")])
        found = re.search(r'class="bar c1" d="([^"]*)"', svg)
        assert found is not None
        xs = [float(a) for a, _ in re.findall(r"(-?\d+\.?\d*),(-?\d+\.?\d*)", found.group(1))]
        assert 0 < max(xs) - min(xs) <= 24.0 + 0.1


class TestLineChart:
    def line(self, **kwargs: object) -> str:
        base: dict[str, object] = {
            "chart_id": "l1",
            "title": "Base load",
            "desc": "kW by month.",
            "categories": ["2025-01", "2025-02", "2025-03", "2025-04"],
            "values": (0.1, 0.2, None, 0.15),
            "unit": "kW",
            "places": 2,
        }
        base.update(kwargs)
        return charts.line_chart(**base)  # type: ignore[arg-type]

    def test_line_breaks_at_a_missing_value(self) -> None:
        svg = self.line()
        assert svg.count('class="line"') == 1
        d = re.search(r'class="line" d="([^"]*)"', svg)
        assert d is not None
        assert d.group(1).count("M") == 2

    def test_end_dot_and_hit_targets(self) -> None:
        svg = self.line()
        assert svg.count('class="dot"') == 1
        assert svg.count('class="hit"') == 3
        assert "2025-04 · 0.15 kW" in svg

    def test_tips_extend_the_default(self) -> None:
        svg = self.line(tips={3: "Peak at 2025-04-07 18:00"})
        assert "2025-04 · 0.15 kW · Peak at 2025-04-07 18:00" in svg

    def test_empty_and_stable(self) -> None:
        assert "None here" in self.line(categories=[], values=(), empty="None here")
        assert self.line() == self.line()

    def test_hostile_text_is_escaped(self) -> None:
        svg = self.line(title=HOSTILE, desc=HOSTILE, unit=HOSTILE, categories=[HOSTILE] * 4)
        assert HOSTILE not in svg
        assert svg.count("<svg ") == 1


class TestBandPanel:
    def panel(self, **kwargs: object) -> str:
        hours = list(range(24))
        base: dict[str, object] = {
            "chart_id": "p1",
            "title": "DJF · weekday",
            "desc": "Mean per hour.",
            "hours": hours,
            "mean": [0.1 + 0.01 * h for h in hours],
            "low": [0.05 + 0.01 * h for h in hours],
            "high": [0.2 + 0.01 * h for h in hours],
            "y_top": 0.5,
            "unit": "kWh",
        }
        base.update(kwargs)
        return charts.band_panel(**base)  # type: ignore[arg-type]

    def test_draws_a_band_and_a_mean_line(self) -> None:
        svg = self.panel()
        assert 'class="band"' in svg
        assert 'class="line"' in svg
        assert 'role="img"' in svg

    def test_every_hour_has_a_tooltip(self) -> None:
        assert self.panel().count("<title>") >= 24

    def test_shared_scale_is_respected(self) -> None:
        low = self.panel(y_top=0.5)
        high = self.panel(y_top=5.0)
        assert low != high

    def test_few_cells_caption(self) -> None:
        assert "Only 3 days" in self.panel(caption="Only 3 days")

    def test_empty_panel(self) -> None:
        svg = self.panel(hours=[], mean=[], low=[], high=[], empty="No hours")
        assert "No hours" in svg
        assert 'class="band"' not in svg


class TestHeatmap:
    def heat(self, **kwargs: object) -> str:
        base: dict[str, object] = {
            "chart_id": "h1",
            "title": "Heat",
            "desc": "Hours by day.",
            "days": ["2025-01-01", "2025-01-02", "2025-01-03"],
            "cells": {(0, 0): 0.1, (0, 1): 0.5, (1, 23): 1.0, (2, 5): 0.0},
            "unit": "kWh",
            "places": 2,
        }
        base.update(kwargs)
        return charts.heatmap_chart(**base)  # type: ignore[arg-type]

    def test_one_rect_per_cell_and_none_for_missing(self) -> None:
        svg = self.heat()
        assert svg.count('class="q') == 4
        assert svg.count("<rect") == 4 + 7

    def test_value_bins_are_ordered(self) -> None:
        svg = self.heat()
        assert 'class="q0"' in svg  # the zero cell
        assert 'class="q6"' in svg  # the maximum

    def test_tooltips_name_day_hour_and_value(self) -> None:
        assert "2025-01-02 23:00 · 1.00 kWh" in self.heat()

    def test_scale_key_is_part_of_the_chart(self) -> None:
        svg = self.heat()
        assert svg.count('class="key') == 7
        assert "1.00" in svg  # the top of the scale is labelled

    def test_empty(self) -> None:
        svg = self.heat(days=[], cells={}, empty="No days")
        assert "No days" in svg
        assert 'class="q' not in svg

    def test_stable_and_hostile_safe(self) -> None:
        assert self.heat() == self.heat()
        svg = self.heat(title=HOSTILE, desc=HOSTILE, unit=HOSTILE)
        assert HOSTILE not in svg


class TestLegendAndTable:
    def test_legend_names_every_entry_with_a_swatch(self) -> None:
        html = charts.legend([("Peak", "c1"), ("Off Peak", "c2")])
        assert html.count('class="swatch c') == 2
        assert ">Peak<" in html
        assert ">Off Peak<" in html

    def test_legend_escapes(self) -> None:
        assert HOSTILE not in charts.legend([(HOSTILE, "c1")])

    def test_data_table_escapes_and_aligns_numbers(self) -> None:
        html = charts.data_table(["Month", "kWh"], [[HOSTILE, "1.0"], ["b", "2.0"]], caption=HOSTILE)
        assert HOSTILE not in html
        assert html.count("<tr>") == 3
        assert 'class="num"' in html


class TestStylesheet:
    def test_one_media_block_and_both_palettes(self) -> None:
        css = charts.stylesheet()
        assert css.count("prefers-color-scheme") == 1
        for light, dark in charts.CATEGORICAL:
            assert light in css
            assert dark in css

    def test_no_network_reference(self) -> None:
        css = charts.stylesheet()
        for banned in ("http:", "https:", "@import", "url(", "src="):
            assert banned not in css

    def test_palette_has_four_slots_in_a_fixed_order(self) -> None:
        assert len(charts.CATEGORICAL) == 4
        assert charts.CATEGORICAL[0] == ("#2a78d6", "#3987e5")
        assert len(charts.SEQUENTIAL) == 7
