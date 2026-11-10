"""`params` v6 (P6 spec §3.1): the `recommend` block and the v5 to v6 migration."""

import copy
from decimal import Decimal
from typing import Any

import pytest

from energatron.core.errors import InvalidInputError
from energatron.core.models.params import Params
from energatron.core.persisted import registered_migrations
from tests.unit.core.test_params_v5 import doc_v5

RECOMMEND = {"top": 5, "usage_scale_pct": "10", "min_saving": "50", "recheck_days": 90, "price_reset": "07-01"}


def doc_v6(**changes: Any) -> dict[str, Any]:
    """A valid v6 document; `changes` replace keys of `recommend`."""
    doc: dict[str, Any] = copy.deepcopy(doc_v5()) | {"schema_version": 6}
    doc["recommend"] = RECOMMEND | changes
    return doc


def test_a_valid_v6_document_validates_and_dumps_decimals_as_strings() -> None:
    params = Params.from_document(doc_v6())
    assert params.schema_version == 6
    assert params.recommend.top == 5
    assert params.recommend.usage_scale_pct == Decimal("10")
    assert params.recommend.min_saving == Decimal("50")
    assert (params.recommend.recheck_days, params.recommend.price_reset) == (90, "07-01")
    dumped = params.model_dump(mode="json")
    assert dumped["recommend"] == RECOMMEND
    assert list(dumped)[-1] == "recommend"


@pytest.mark.parametrize(
    "changes",
    [
        {"top": 0},
        {"top": "5"},
        {"usage_scale_pct": "0"},
        {"usage_scale_pct": "100"},
        {"usage_scale_pct": "NaN"},
        {"min_saving": "-1"},
        {"recheck_days": 0},
        {"price_reset": "13-01"},
        {"price_reset": "02-30"},
        {"price_reset": "7-1"},
    ],
)
def test_invalid_recommend_values_name_their_field(changes: dict[str, Any]) -> None:
    with pytest.raises(InvalidInputError) as caught:
        Params.from_document(doc_v6(**changes))
    assert "recommend" in str(caught.value.details)


def test_the_29th_of_february_is_a_valid_reset_day() -> None:
    assert Params.from_document(doc_v6(price_reset="02-29")).recommend.price_reset == "02-29"


def test_the_v5_to_v6_migration_inserts_the_defaults_and_keeps_every_v5_value() -> None:
    migrate = registered_migrations()[("params", 5)]
    old = doc_v5()
    new = migrate(copy.deepcopy(old))
    assert new["schema_version"] == 6
    assert new["recommend"] == RECOMMEND
    assert {key: value for key, value in new.items() if key not in ("schema_version", "recommend")} == {
        key: value for key, value in old.items() if key != "schema_version"
    }
    assert Params.from_document(doc_v5()).recommend == Params.from_document(doc_v6()).recommend
