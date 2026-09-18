"""Load and refresh the official U.S. Senate contact snapshot.

Reports deliberately read only the checked-in snapshot.  Refreshing it is a
separate, explicit command so a report can be reproduced without network
access.
"""

import hashlib
import json
import os
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

import requests

SENATE_SOURCE_URL = "https://www.senate.gov/general/contact_information/senators_cfm.xml"
SNAPSHOT_DIRECTORY = Path(__file__).resolve().parents[2] / "data/reference/senate"
SNAPSHOT_XML_PATH = SNAPSHOT_DIRECTORY / "senators.xml"
SNAPSHOT_METADATA_PATH = SNAPSHOT_DIRECTORY / "senators.json"
US_STATE_CODES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
    "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV "
    "WI WY".split()
)


class SenateDataError(ValueError):
    """Raise when the Senate reference snapshot is unsafe to use."""


@dataclass(frozen=True)
class SenatorContact:
    """One senator's public Washington contact details."""

    name: str
    state: str
    address: str
    phone: str
    contact_form_url: str


@dataclass(frozen=True)
class SenateSnapshot:
    """Validated contacts plus the provenance stored with their XML."""

    senators: tuple[SenatorContact, ...]
    source_url: str
    retrieved_at: datetime


def parse_senators_xml(xml: bytes | str) -> tuple[SenatorContact, ...]:
    """Parse Senate.gov XML and require all display fields for every member."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as error:
        raise SenateDataError("Senate snapshot XML is malformed.") from error

    contacts = []
    for member in root.findall(".//member"):
        values = {
            name: _xml_text(member, name)
            for name in ("member_full", "state", "address", "phone", "email")
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise SenateDataError(
                "Senate XML has a member with missing required field(s): "
                + ", ".join(missing)
                + "."
            )
        state = values["state"].upper()
        if state not in US_STATE_CODES:
            raise SenateDataError(f"Senate XML has an invalid state code: {state!r}.")
        contact_url = values["email"]
        if not contact_url.startswith(("https://", "http://")):
            raise SenateDataError(
                f"Senate XML has an invalid contact-form URL for {values['member_full']}."
            )
        contacts.append(
            SenatorContact(
                name=values["member_full"],
                state=state,
                address=values["address"],
                phone=values["phone"],
                contact_form_url=contact_url,
            )
        )
    if not contacts:
        raise SenateDataError("Senate XML contains no senator records.")
    return tuple(contacts)


def senators_for_state(
    senators: tuple[SenatorContact, ...] | list[SenatorContact], state: str
) -> tuple[SenatorContact, SenatorContact]:
    """Return exactly two contacts for a two-letter state code."""
    normalized_state = state.strip().upper()
    if normalized_state not in US_STATE_CODES:
        raise SenateDataError(f"State must be a two-letter U.S. state code, not {state!r}.")
    selected = tuple(senator for senator in senators if senator.state == normalized_state)
    if len(selected) != 2:
        raise SenateDataError(
            f"Senate snapshot must contain exactly two senators for {normalized_state}; "
            f"found {len(selected)}."
        )
    return selected[0], selected[1]


def validate_senators(senators: tuple[SenatorContact, ...]) -> None:
    """Ensure a refresh supplies precisely two usable contacts for every state."""
    counts = Counter(senator.state for senator in senators)
    invalid = sorted(state for state in counts if state not in US_STATE_CODES)
    missing_or_wrong_count = sorted(
        state for state in US_STATE_CODES if counts[state] != 2
    )
    if invalid or missing_or_wrong_count:
        details = []
        if missing_or_wrong_count:
            details.append(
                "states without exactly two senators: "
                + ", ".join(missing_or_wrong_count)
            )
        if invalid:
            details.append("invalid state codes: " + ", ".join(invalid))
        raise SenateDataError("Invalid Senate contact set; " + "; ".join(details) + ".")


def load_snapshot(
    xml_path: Path | str = SNAPSHOT_XML_PATH,
    metadata_path: Path | str = SNAPSHOT_METADATA_PATH,
) -> SenateSnapshot:
    """Load a local snapshot after checking its metadata and checksum."""
    xml_path, metadata_path = Path(xml_path), Path(metadata_path)
    if not xml_path.is_file() or not metadata_path.is_file():
        raise SenateDataError(
            "Senate snapshot is missing. Run `uv run aisc_gr_statistics refresh-senators`."
        )
    try:
        xml = xml_path.read_bytes()
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SenateDataError("Senate snapshot metadata is malformed or unreadable.") from error
    required = {"source_url", "retrieved_at", "xml_sha256"}
    if not isinstance(metadata, dict) or not required <= metadata.keys():
        raise SenateDataError("Senate snapshot metadata is missing required fields.")
    checksum = hashlib.sha256(xml).hexdigest()
    if metadata["xml_sha256"] != checksum:
        raise SenateDataError("Senate snapshot checksum does not match senators.xml.")
    try:
        retrieved_at = datetime.fromisoformat(str(metadata["retrieved_at"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise SenateDataError("Senate snapshot retrieval timestamp is invalid.") from error
    if retrieved_at.tzinfo is None or not isinstance(metadata["source_url"], str):
        raise SenateDataError("Senate snapshot metadata is malformed.")
    senators = parse_senators_xml(xml)
    validate_senators(senators)
    return SenateSnapshot(senators, metadata["source_url"], retrieved_at)


def refresh_snapshot(
    xml_path: Path | str = SNAPSHOT_XML_PATH,
    metadata_path: Path | str = SNAPSHOT_METADATA_PATH,
    *,
    source_url: str = SENATE_SOURCE_URL,
    get=requests.get,
    now=None,
) -> SenateSnapshot:
    """Download, validate, then replace both snapshot files.

    Validation happens before either existing file changes, so a failed request
    or bad response leaves the last known-good snapshot intact.
    """
    try:
        response = get(source_url, timeout=30)
        response.raise_for_status()
    except requests.RequestException as error:
        raise SenateDataError(f"Could not download Senate contact XML: {error}") from error
    xml = response.content
    senators = parse_senators_xml(xml)
    validate_senators(senators)
    retrieved_at = (now() if now else datetime.now(UTC)).astimezone(UTC)
    metadata = {
        "source_url": source_url,
        "retrieved_at": retrieved_at.isoformat().replace("+00:00", "Z"),
        "xml_sha256": hashlib.sha256(xml).hexdigest(),
    }
    _replace_snapshot_files(Path(xml_path), Path(metadata_path), xml, metadata)
    return SenateSnapshot(senators, source_url, retrieved_at)


def _xml_text(member: ElementTree.Element, name: str) -> str:
    return (member.findtext(name) or "").strip()


def _replace_snapshot_files(
    xml_path: Path, metadata_path: Path, xml: bytes, metadata: dict[str, str]
) -> None:
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    xml_temp = _temporary_file(xml_path.parent, xml)
    metadata_temp = _temporary_file(
        metadata_path.parent, (json.dumps(metadata, indent=2) + "\n").encode()
    )
    try:
        os.replace(xml_temp, xml_path)
        os.replace(metadata_temp, metadata_path)
    finally:
        for temporary_path in (xml_temp, metadata_temp):
            Path(temporary_path).unlink(missing_ok=True)


def _temporary_file(directory: Path, content: bytes) -> str:
    descriptor, path = tempfile.mkstemp(dir=directory, prefix=".senate-")
    with os.fdopen(descriptor, "wb") as file_handle:
        file_handle.write(content)
    return path
