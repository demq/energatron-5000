"""The recommendation page (P6 spec §7.4): golden HTML for a synthetic document, escaping, no script, same bytes."""

import json
import os
from pathlib import Path
from typing import Any

from energatron.core.models.recommend import Recommendation
from energatron.core.recommendation_report import render

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
GOLDEN = FIXTURES / "golden" / "recommendation.html"
DOCUMENT = FIXTURES / "schemas" / "recommendation" / "v1.json"


def synthetic(**changes: Any) -> Recommendation:
    return Recommendation.from_document(json.loads(DOCUMENT.read_text(encoding="utf-8")) | changes)


def test_the_page_matches_the_golden_file() -> None:
    page = render(synthetic())
    if os.environ.get("ENERGATRON_UPDATE_GOLDEN") == "1":  # regenerate after a deliberate change
        GOLDEN.write_text(page, encoding="utf-8")
    assert page == GOLDEN.read_text(encoding="utf-8")


def test_the_page_is_self_contained_and_deterministic() -> None:
    page = render(synthetic())
    assert page == render(synthetic())
    assert "<script" not in page
    assert "http://" not in page and "https://" not in page
    assert "REC-20261101-0123abcd" in page
    assert "Switch" in page


def test_text_from_the_document_is_escaped() -> None:
    doc = json.loads(DOCUMENT.read_text(encoding="utf-8"))
    doc["priced"]["electricity"][0]["display_name"] = "<b>Bold & co</b>"
    page = render(Recommendation.from_document(doc))
    assert "<b>Bold" not in page
    assert "&lt;b&gt;Bold &amp; co&lt;/b&gt;" in page


def test_a_partial_gas_year_is_flagged_and_stay_says_stay() -> None:
    page = render(synthetic(action="stay", robust=False))
    assert "less than a year" in page
    assert "Stay on the current plans" in page
    assert "does <strong>not</strong> hold" in page
