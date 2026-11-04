"""`offer_cdr_index` (P4 spec §3.3): the fetch log's parser, and that it and `offer_cdr` never mistake each other."""

import json
from datetime import UTC, datetime

import pytest

from energatron.adapters.sources.cdr_index import OFFER_CDR_INDEX
from energatron.adapters.sources.cdr_plan import OFFER_CDR
from energatron.core.errors import InvalidInputError
from energatron.core.models.catalog import SourceType
from energatron.core.models.offers import OfferFetchLog
from tests.fixtures import make_cdr as cdr

PLANS = [cdr.plan("TEST1@EME"), cdr.plan("TEST2@EME")]
LOG = cdr.fetch_log(retailers=[("alpha", PLANS)], selected=[cdr.selected("TEST1@EME"), cdr.selected("TEST2@EME")])
BODY = cdr.fetch_log_body(LOG)


def test_the_parser_is_a_source_parser_for_offer_cdr_index_version_1() -> None:
    assert (OFFER_CDR_INDEX.source, OFFER_CDR_INDEX.version) == (SourceType.OFFER_CDR_INDEX, 1)


def test_a_fetch_log_is_detected_by_its_kind() -> None:
    detection = OFFER_CDR_INDEX.detect("fetch-log.json", BODY[:65536])
    assert detection is not None
    assert detection.evidence == ("content",)


def test_a_fetch_log_with_long_pages_is_still_detected_from_its_head() -> None:
    log = cdr.fetch_log(retailers=[("alpha", [cdr.plan(f"TEST{n}@EME") for n in range(300)])])
    body = cdr.fetch_log_body(log)
    assert len(body) > 65536
    assert OFFER_CDR_INDEX.detect("fetch-log.json", body[:65536]) is not None


def test_the_two_offer_parsers_never_detect_each_others_files() -> None:
    plan_body = cdr.detail_body(PLANS[0])
    assert OFFER_CDR.detect("fetch-log.json", BODY[:65536]) is None
    assert OFFER_CDR_INDEX.detect("alpha__TEST1@EME.json", plan_body) is None
    assert OFFER_CDR.detect("alpha__TEST1@EME.json", plan_body) is not None


def test_inspect_gives_the_batch_time_and_facts_without_identifiers() -> None:
    info = OFFER_CDR_INDEX.inspect("fetch-log.json", BODY)
    assert info.source_ts == datetime(2026, 11, 1, tzinfo=UTC)
    assert info.coverage is None
    assert info.extra == {
        "fetch_batch_id": "fetch-20261101T000000Z",
        "retailers": ["alpha"],
        "selected": 2,
        "source_ts_from": "content",
    }
    assert info.identifiers == {}
    assert info.extension == ".json"


def test_the_logged_pages_decode_to_the_list_bodies_verbatim() -> None:
    log = OfferFetchLog.from_document(json.loads(BODY))
    (page,) = log.retailers[0].pages
    assert page.body() == cdr.list_body(PLANS, self_url="https://cdr.test/alpha/plans")


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"{not json", "bad_json"),
        (b"[1, 2]", "not_a_fetch_log"),
        (json.dumps(LOG | {"fetch_batch_id": "batch-1"}).encode(), "invalid_fetch_log"),
        (json.dumps(LOG | {"schema_version": 9}).encode(), "invalid_fetch_log"),
        (json.dumps({**LOG, "filter": {**LOG["filter"], "postcode": "<postcode>"}}).encode(), "invalid_fetch_log"),
    ],
)
def test_malformed_fetch_logs_are_rejected_with_a_reason(data: bytes, reason: str) -> None:
    with pytest.raises(InvalidInputError) as caught:
        OFFER_CDR_INDEX.inspect("fetch-log.json", data)
    assert caught.value.details["reason"] == reason
