"""Tests for the offline Clerk-authoritative House snapshot."""

import hashlib
import json
from datetime import UTC, datetime

import pytest
import requests

from aisc_gr_statistics.house import (
    HouseContact,
    HouseDataError,
    enrich_members,
    load_snapshot,
    members_for_state,
    parse_directory_html,
    parse_members_xml,
    refresh_snapshot,
)

MEMBERS_XML = b"""<MemberData publish-date="September 2, 2026"><members>
<member><member-info><official-name>Example Member</official-name><party>D</party><state postal-code="IL"/><district>1st</district><office-building>LHOB</office-building><office-room>100</office-room><office-zip>20515</office-zip><phone>(202) 225-0001</phone></member-info></member>
<member><member-info><official-name>Another Member</official-name><party>R</party><state postal-code="CA"/><district>At Large</district><office-building>RHOB</office-building><office-room>200</office-room><office-zip>20515</office-zip><phone>(202) 225-0002</phone></member-info></member>
</members></MemberData>"""

DIRECTORY_HTML = """<table><caption>Illinois</caption><tr><td>1st</td><td><a href="https://example.house.gov">Member</a><img src="/photo.jpg"></td></tr></table>"""


def test_clerk_xml_is_the_authority_and_directory_only_adds_urls():
    members = parse_members_xml(MEMBERS_XML)
    enriched = enrich_members(members, parse_directory_html(DIRECTORY_HTML))

    assert enriched[0].name == "Example Member"
    assert enriched[0].district == "1"
    assert (
        enriched[0].address
        == "100 Longworth House Office Building\nWashington, DC 20515"
    )
    assert enriched[0].website_url == "https://example.house.gov"
    assert enriched[0].contact_form_url == "https://example.house.gov/contact"
    assert enriched[0].photo_url == "https://www.house.gov/photo.jpg"
    assert enriched[1].website_url == ""


def test_vacancy_is_explicit_and_never_uses_predecessor_data():
    vacant_xml = MEMBERS_XML.replace(
        b"<official-name>Example Member</official-name><party>D</party>",
        b"<official-name/><party/><footnote>Vacancy due to resignation.</footnote>",
    )

    member = parse_members_xml(vacant_xml)[0]

    assert member == HouseContact("Vacant", "", "IL", "1", vacant=True)


@pytest.mark.parametrize(
    "replacement", [b"<district>0th</district>", b"<district>1st</district>"]
)
def test_rejects_invalid_or_duplicate_seats(replacement):
    xml = MEMBERS_XML.replace(b"<district>At Large</district>", replacement)
    if replacement == b"<district>1st</district>":
        xml = xml.replace(b'postal-code="CA"', b'postal-code="IL"')
    with pytest.raises(HouseDataError):
        parse_members_xml(xml)


def test_illinois_requires_all_seventeen_current_districts():
    with pytest.raises(HouseDataError, match="1 through 17"):
        members_for_state(parse_members_xml(MEMBERS_XML), "IL")


def test_load_snapshot_rejects_checksum_and_provenance_seat_mismatch(tmp_path):
    xml_path, contacts_path, metadata_path = (
        tmp_path / name for name in ("members.xml", "contacts.json", "metadata.json")
    )
    xml_path.write_bytes(MEMBERS_XML)
    contacts_path.write_text(json.dumps([]), encoding="utf-8")
    metadata_path.write_text(
        json.dumps(
            {
                "clerk_source_url": "https://clerk.house.gov/xml/lists/MemberData.xml",
                "directory_source_url": "https://www.house.gov/representatives",
                "retrieved_at": "2026-01-01T00:00:00Z",
                "xml_sha256": hashlib.sha256(MEMBERS_XML).hexdigest(),
                "contacts_sha256": hashlib.sha256(b"[]").hexdigest(),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(HouseDataError, match="do not match"):
        load_snapshot(xml_path, contacts_path, metadata_path)


class _Response:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None


def test_failed_refresh_preserves_every_previous_snapshot_file(tmp_path):
    paths = tuple(
        tmp_path / name for name in ("members.xml", "contacts.json", "metadata.json")
    )
    for path in paths:
        path.write_text("old", encoding="utf-8")

    with pytest.raises(HouseDataError):
        refresh_snapshot(
            *paths,
            get=lambda url, timeout: (_ for _ in ()).throw(
                requests.ConnectionError("offline")
            ),
        )

    assert [path.read_text(encoding="utf-8") for path in paths] == ["old", "old", "old"]


def test_refresh_writes_checksums_for_valid_download(tmp_path):
    paths = tuple(
        tmp_path / name for name in ("members.xml", "contacts.json", "metadata.json")
    )
    snapshot = refresh_snapshot(
        *paths,
        get=lambda url, timeout: _Response(
            MEMBERS_XML if "MemberData" in url else DIRECTORY_HTML.encode()
        ),
        now=lambda: datetime(2026, 1, 2, tzinfo=UTC),
    )
    metadata = json.loads(paths[2].read_text(encoding="utf-8"))

    assert len(snapshot.members) == 2
    assert metadata["retrieved_at"] == "2026-01-02T00:00:00Z"
    assert metadata["xml_sha256"] == hashlib.sha256(MEMBERS_XML).hexdigest()
