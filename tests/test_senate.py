"""Tests for the offline Senate.gov reference-data snapshot."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import requests

from aisc_gr_statistics.senate import (
    SenateDataError,
    SenatorContact,
    load_snapshot,
    parse_senators_xml,
    refresh_snapshot,
    senators_for_state,
)

ILLINOIS_XML = b"""<?xml version=\"1.0\"?>
<contact_information>
  <member><member_full>First (D-IL)</member_full><state>IL</state><address>1 Senate Office Building Washington DC 20510</address><phone>(202) 224-0001</phone><email>https://example.senate.gov/contact</email></member>
  <member><member_full>Second (R-IL)</member_full><state>IL</state><address>2 Senate Office Building Washington DC 20510</address><phone>(202) 224-0002</phone><email>https://second.senate.gov/contact</email></member>
</contact_information>"""


def test_parses_illinois_contacts_and_preserves_public_contact_fields():
    senators = parse_senators_xml(ILLINOIS_XML)

    assert senators_for_state(senators, "il") == (
        SenatorContact(
            "First (D-IL)",
            "IL",
            "1 Senate Office Building Washington DC 20510",
            "(202) 224-0001",
            "https://example.senate.gov/contact",
        ),
        SenatorContact(
            "Second (R-IL)",
            "IL",
            "2 Senate Office Building Washington DC 20510",
            "(202) 224-0002",
            "https://second.senate.gov/contact",
        ),
    )


def test_rejects_member_without_required_contact_field():
    incomplete = ILLINOIS_XML.replace(b"<phone>(202) 224-0001</phone>", b"")

    with pytest.raises(SenateDataError, match="missing required field"):
        parse_senators_xml(incomplete)


def test_requires_exactly_two_contacts_for_requested_state():
    one_contact = parse_senators_xml(ILLINOIS_XML)[:1]

    with pytest.raises(SenateDataError, match="exactly two senators for IL"):
        senators_for_state(one_contact, "IL")


def test_load_snapshot_rejects_checksum_mismatch(tmp_path):
    xml_path = tmp_path / "senators.xml"
    metadata_path = tmp_path / "senators.json"
    xml_path.write_bytes(ILLINOIS_XML)
    metadata_path.write_text(
        json.dumps(
            {
                "source_url": "https://example.test/senators.xml",
                "retrieved_at": "2026-01-01T00:00:00Z",
                "xml_sha256": "not-a-real-checksum",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SenateDataError, match="checksum"):
        load_snapshot(xml_path, metadata_path)


class _Response:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None


def test_refresh_writes_matching_snapshot_files_from_valid_download(tmp_path):
    # The committed XML is a complete 50-state fixture, suitable for refresh
    # validation while keeping this test independent of the network.
    complete_xml = Path("data/reference/senate/senators.xml")
    downloaded = complete_xml.read_bytes()
    xml_path = tmp_path / "senators.xml"
    metadata_path = tmp_path / "senators.json"

    snapshot = refresh_snapshot(
        xml_path,
        metadata_path,
        get=lambda url, timeout: _Response(downloaded),
        now=lambda: datetime(2026, 1, 2, tzinfo=UTC),
    )

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert len(snapshot.senators) == 100
    assert xml_path.read_bytes() == downloaded
    assert metadata["xml_sha256"] == hashlib.sha256(downloaded).hexdigest()
    assert metadata["retrieved_at"] == "2026-01-02T00:00:00Z"


@pytest.mark.parametrize(
    "get",
    [
        lambda url, timeout: (_ for _ in ()).throw(requests.ConnectionError("offline")),
        lambda url, timeout: _Response(ILLINOIS_XML),
    ],
)
def test_failed_or_invalid_refresh_leaves_existing_snapshot_unchanged(tmp_path, get):
    xml_path = tmp_path / "senators.xml"
    metadata_path = tmp_path / "senators.json"
    xml_path.write_bytes(b"old XML")
    metadata_path.write_text("old metadata", encoding="utf-8")

    with pytest.raises(SenateDataError):
        refresh_snapshot(xml_path, metadata_path, get=get)

    assert xml_path.read_bytes() == b"old XML"
    assert metadata_path.read_text(encoding="utf-8") == "old metadata"
