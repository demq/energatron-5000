"""`services.datasets` (P9 spec §3.1): counts, order, filter, limit, and nothing from the file beyond its dates."""

import json
from pathlib import Path

import pytest

from energatron.core.errors import UsageError
from energatron.core.models.catalog import SourceType
from energatron.services.datasets import datasets
from tests.unit.services._read_workspace import context, make_read_workspace


def test_counts_every_source_and_lists_the_newest_first(tmp_path: Path) -> None:
    paths, landed = make_read_workspace(tmp_path)
    result = datasets(context(paths))
    assert set(result.counts) == {source.value for source in SourceType}
    assert (
        result.counts["gas_bill"] == 1 and result.counts["offer_cdr_index"] == 1 and result.counts["sapn_interval"] == 0
    )
    assert (result.total, result.shown) == (2, 2)
    assert [entry.dataset_id for entry in result.datasets] == [landed[1].dataset_id, landed[0].dataset_id]
    bill = result.datasets[0]
    assert bill.source is SourceType.GAS_BILL and bill.detector.startswith("gas_bill v")
    assert bill.extracted_by is None and bill.supersedes is None


def test_filter_and_limit(tmp_path: Path) -> None:
    paths, landed = make_read_workspace(tmp_path)
    result = datasets(context(paths), source=SourceType.OFFER_CDR_INDEX)
    assert (result.total, [entry.dataset_id for entry in result.datasets]) == (1, [landed[0].dataset_id])
    limited = datasets(context(paths), limit=1)
    assert (limited.total, limited.shown) == (2, 1)


@pytest.mark.parametrize("limit", [0, 1001])
def test_a_limit_out_of_range_is_a_usage_error(tmp_path: Path, limit: int) -> None:
    paths, _ = make_read_workspace(tmp_path)
    with pytest.raises(UsageError):
        datasets(context(paths), limit=limit)


def test_no_file_name_note_or_stored_path(tmp_path: Path) -> None:
    paths, _ = make_read_workspace(tmp_path)
    text = json.dumps(datasets(context(paths)).model_dump(mode="json"))
    for leaked in ("Private Name", "fetch-log.json", "synthetic", "stored_path", "original_filename", "notes", "bytes"):
        assert leaked not in text
