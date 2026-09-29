"""Load and refresh the official U.S. House current-member snapshot.

The Clerk XML decides who currently holds a seat.  The House directory is
only a supplement for links that make a printed contact card useful.  Reports
read these checked-in files and never contact either web site themselves.
"""

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

import requests

from .senate import US_STATE_CODES

HOUSE_JURISDICTION_CODES = US_STATE_CODES | frozenset(
    {"AS", "DC", "GU", "MP", "PR", "VI"}
)

CLERK_SOURCE_URL = "https://clerk.house.gov/xml/lists/MemberData.xml"
DIRECTORY_SOURCE_URL = "https://www.house.gov/representatives"
SNAPSHOT_DIRECTORY = Path(__file__).resolve().parents[2] / "data/reference/house"
SNAPSHOT_XML_PATH = SNAPSHOT_DIRECTORY / "members.xml"
SNAPSHOT_CONTACTS_PATH = SNAPSHOT_DIRECTORY / "contacts.json"
SNAPSHOT_METADATA_PATH = SNAPSHOT_DIRECTORY / "metadata.json"


class HouseDataError(ValueError):
    """Raise when the House reference snapshot is unsafe to use."""


@dataclass(frozen=True)
class HouseContact:
    """One current House seat and its public Washington contact details."""

    name: str
    party: str
    state: str
    district: str
    address: str = ""
    phone: str = ""
    contact_form_url: str = ""
    website_url: str = ""
    photo_url: str = ""
    vacant: bool = False


@dataclass(frozen=True)
class HouseSnapshot:
    """Validated current contacts and source provenance."""

    members: tuple[HouseContact, ...]
    clerk_source_url: str
    directory_source_url: str
    retrieved_at: datetime
    source_publication_date: str = ""


def parse_members_xml(xml: bytes | str) -> tuple[HouseContact, ...]:
    """Parse Clerk current-member XML without filling seats from old records."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as error:
        raise HouseDataError("House snapshot XML is malformed.") from error

    members = []
    for record in root.findall(".//members/member"):
        info = record.find("member-info")
        if info is None:
            info = record
        state = (
            (
                info.find("state").get("postal-code", "")
                if info.find("state") is not None
                else ""
            )
            .strip()
            .upper()
        )
        district = _normalize_district(_text(info, "district"))
        vacant = (
            record.get("vacant", "").lower() == "true"
            or _text(record, "vacancy").lower() == "true"
            # The Clerk's current feed represents a vacancy as a seat with
            # empty member fields and a current ``footnote`` explaining it.
            # Predecessor data exists nearby, but is intentionally ignored.
            or (
                not _text(info, "official-name")
                and "vacan" in _text(info, "footnote").lower()
            )
        )
        if not state or not district:
            raise HouseDataError(
                "House XML has a member with missing state or district."
            )
        if state not in HOUSE_JURISDICTION_CODES or not _valid_district(district):
            raise HouseDataError(
                f"House XML has an invalid state/district: {state}-{district}."
            )
        if vacant:
            members.append(HouseContact("Vacant", "", state, district, vacant=True))
            continue
        values = {
            "name": _text(info, "official-name"),
            "party": _text(info, "party"),
            "office-building": _text(info, "office-building"),
            "office-room": _text(info, "office-room"),
            "office-zip": _text(info, "office-zip"),
            "phone": _text(info, "phone"),
        }
        missing = [field for field, value in values.items() if not value]
        if missing:
            raise HouseDataError(
                "House XML has a current member with missing required field(s): "
                + ", ".join(missing)
                + "."
            )
        if values["party"] not in {"D", "R", "I", "L"}:
            raise HouseDataError(
                f"House XML has an invalid party for {values['name']}."
            )
        building = {
            "CHOB": "Cannon House Office Building",
            "LHOB": "Longworth House Office Building",
            "RHOB": "Rayburn House Office Building",
        }.get(values["office-building"], values["office-building"])
        address = (
            f"{values['office-room']} {building}\nWashington, DC {values['office-zip']}"
        )
        members.append(
            HouseContact(
                values["name"],
                values["party"],
                state,
                district,
                address,
                values["phone"],
            )
        )
    if not members:
        raise HouseDataError("House XML contains no current-member records.")
    validate_members(tuple(members))
    return tuple(members)


def parse_directory_html(html: bytes | str) -> dict[tuple[str, str], dict[str, str]]:
    """Read official directory links keyed by state and district.

    The directory is deliberately unable to create a member: its values are
    attached only to seats already found in Clerk XML.
    """
    parser = _DirectoryParser()
    parser.feed(html.decode() if isinstance(html, bytes) else html)
    return parser.entries


def enrich_members(
    members: tuple[HouseContact, ...], directory: dict[tuple[str, str], dict[str, str]]
) -> tuple[HouseContact, ...]:
    """Add only directory links to current Clerk seats."""
    enriched = []
    for member in members:
        supplement = directory.get((member.state, member.district), {})
        website = supplement.get("website_url", "")
        contact = supplement.get("contact_form_url", "") or (
            website.rstrip("/") + "/contact" if website and not member.vacant else ""
        )
        values = asdict(member)
        values.update(
            website_url=website,
            contact_form_url=contact,
            photo_url=supplement.get("photo_url", ""),
        )
        enriched.append(HouseContact(**values))
    return tuple(enriched)


def validate_members(members: tuple[HouseContact, ...]) -> None:
    """Reject duplicate seats and malformed records before writing a snapshot."""
    seats = set()
    for member in members:
        seat = (member.state, member.district)
        if member.state not in HOUSE_JURISDICTION_CODES or not _valid_district(
            member.district
        ):
            raise HouseDataError(
                f"Invalid House seat: {member.state}-{member.district}."
            )
        if seat in seats:
            raise HouseDataError(
                f"House snapshot has duplicate seat: {member.state}-{member.district}."
            )
        seats.add(seat)
        if member.vacant:
            continue
        if not all((member.name, member.party, member.address, member.phone)):
            raise HouseDataError(
                f"House snapshot has incomplete current member: {seat}."
            )
        for url in (member.contact_form_url, member.website_url, member.photo_url):
            if url and not url.startswith(("https://", "http://")):
                raise HouseDataError(
                    f"House snapshot has an invalid URL for {member.name}."
                )


def members_for_state(
    members: tuple[HouseContact, ...], state: str
) -> tuple[HouseContact, ...]:
    """Return a state's seats in district order, requiring Illinois's 17 seats."""
    state = state.strip().upper()
    selected = tuple(
        sorted((m for m in members if m.state == state), key=_district_key)
    )
    if state == "IL" and tuple(member.district for member in selected) != tuple(
        str(i) for i in range(1, 18)
    ):
        raise HouseDataError(
            "House snapshot must contain Illinois districts 1 through 17 exactly once."
        )
    return selected


def load_snapshot(
    xml_path: Path | str = SNAPSHOT_XML_PATH,
    contacts_path: Path | str = SNAPSHOT_CONTACTS_PATH,
    metadata_path: Path | str = SNAPSHOT_METADATA_PATH,
) -> HouseSnapshot:
    """Load and integrity-check the local House files."""
    xml_path, contacts_path, metadata_path = map(
        Path, (xml_path, contacts_path, metadata_path)
    )
    if not all(path.is_file() for path in (xml_path, contacts_path, metadata_path)):
        raise HouseDataError(
            "House snapshot is missing. Run `uv run aisc_gr_statistics refresh-representatives`."
        )
    try:
        xml, contacts_bytes = xml_path.read_bytes(), contacts_path.read_bytes()
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        contacts = tuple(HouseContact(**row) for row in json.loads(contacts_bytes))
    except (OSError, json.JSONDecodeError, TypeError) as error:
        raise HouseDataError("House snapshot is malformed or unreadable.") from error
    required = {
        "clerk_source_url",
        "directory_source_url",
        "retrieved_at",
        "xml_sha256",
        "contacts_sha256",
    }
    if not isinstance(metadata, dict) or not required <= metadata.keys():
        raise HouseDataError("House snapshot metadata is missing required fields.")
    if (
        metadata["xml_sha256"] != hashlib.sha256(xml).hexdigest()
        or metadata["contacts_sha256"] != hashlib.sha256(contacts_bytes).hexdigest()
    ):
        raise HouseDataError("House snapshot checksum does not match its data files.")
    try:
        retrieved_at = datetime.fromisoformat(
            str(metadata["retrieved_at"]).replace("Z", "+00:00")
        )
    except ValueError as error:
        raise HouseDataError(
            "House snapshot retrieval timestamp is invalid."
        ) from error
    if retrieved_at.tzinfo is None or not all(
        isinstance(metadata[key], str)
        for key in ("clerk_source_url", "directory_source_url")
    ):
        raise HouseDataError("House snapshot metadata is malformed.")
    parsed = parse_members_xml(xml)
    clerk_seats = {(member.state, member.district, member.vacant) for member in parsed}
    if {
        (member.state, member.district, member.vacant) for member in contacts
    } != clerk_seats:
        raise HouseDataError(
            "House contacts do not match the current Clerk-member seats."
        )
    validate_members(contacts)
    return HouseSnapshot(
        contacts,
        metadata["clerk_source_url"],
        metadata["directory_source_url"],
        retrieved_at,
        metadata.get("source_publication_date", ""),
    )


def refresh_snapshot(
    xml_path: Path | str = SNAPSHOT_XML_PATH,
    contacts_path: Path | str = SNAPSHOT_CONTACTS_PATH,
    metadata_path: Path | str = SNAPSHOT_METADATA_PATH,
    *,
    clerk_source_url: str = CLERK_SOURCE_URL,
    directory_source_url: str = DIRECTORY_SOURCE_URL,
    get=requests.get,
    now=None,
) -> HouseSnapshot:
    """Download, validate, then atomically replace all House snapshot files."""
    try:
        xml_response, directory_response = (
            get(clerk_source_url, timeout=30),
            get(directory_source_url, timeout=30),
        )
        xml_response.raise_for_status()
        directory_response.raise_for_status()
    except requests.RequestException as error:
        raise HouseDataError(
            f"Could not download House reference data: {error}"
        ) from error
    xml = xml_response.content
    members = enrich_members(
        parse_members_xml(xml), parse_directory_html(directory_response.content)
    )
    validate_members(members)
    retrieved_at = (now() if now else datetime.now(UTC)).astimezone(UTC)
    contacts_bytes = (
        json.dumps([asdict(member) for member in members], indent=2) + "\n"
    ).encode()
    metadata = {
        "clerk_source_url": clerk_source_url,
        "directory_source_url": directory_source_url,
        "retrieved_at": retrieved_at.isoformat().replace("+00:00", "Z"),
        "source_publication_date": ElementTree.fromstring(xml).get("publish-date", ""),
        "xml_sha256": hashlib.sha256(xml).hexdigest(),
        "contacts_sha256": hashlib.sha256(contacts_bytes).hexdigest(),
    }
    _replace_files(
        (Path(xml_path), xml),
        (Path(contacts_path), contacts_bytes),
        (Path(metadata_path), (json.dumps(metadata, indent=2) + "\n").encode()),
    )
    return HouseSnapshot(
        members,
        clerk_source_url,
        directory_source_url,
        retrieved_at,
        metadata["source_publication_date"],
    )


def _text(element: ElementTree.Element, name: str) -> str:
    return (element.findtext(name) or "").strip()


def _normalize_district(value: str) -> str:
    value = value.strip()
    if value.casefold() in {"at large", "at-large", "00"}:
        return "AL"
    match = re.fullmatch(r"(\d+)(?:st|nd|rd|th)?", value, re.I)
    return str(int(match.group(1))) if match else value


def _valid_district(value: str) -> bool:
    return value in {"AL", "Delegate", "Resident Commissioner"} or (
        value.isdigit() and 1 <= int(value) <= 53
    )


def _district_key(member: HouseContact) -> int:
    return 0 if member.district == "AL" else int(member.district)


class _DirectoryParser(HTMLParser):
    """Small stdlib parser for the House directory's state tables."""

    def __init__(self):
        super().__init__()
        self.entries = {}
        self.state = ""
        self._heading = False
        self._row = []
        self._row_photo = ""
        self._cell = []
        self._href = ""
        self._in_row = False
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in {"h2", "h3", "caption"}:
            self._heading = True
        if tag == "tr":
            self._in_row = True
            self._row = []
            self._row_photo = ""
        if self._in_row and tag in {"td", "th"}:
            self._in_cell = True
            self._cell = []
            self._href = ""
        if self._in_cell and tag == "a":
            self._href = attributes.get("href", "")
        if self._in_row and tag == "img":
            self._row_photo = attributes.get("src", "")

    def handle_data(self, data):
        if self._heading:
            self.state = _state_from_name(data.strip()) or self.state
        if self._in_cell:
            self._cell.append(data.strip())

    def handle_endtag(self, tag):
        if tag in {"h2", "h3", "caption"}:
            self._heading = False
        if self._in_row and tag in {"td", "th"}:
            self._row.append((" ".join(filter(None, self._cell)), self._href))
            self._in_cell = False
        if tag == "tr":
            if self.state in HOUSE_JURISDICTION_CODES and len(self._row) >= 2:
                district = _normalize_district(self._row[0][0])
                url = next((href for _, href in self._row if ".house.gov" in href), "")
                if _valid_district(district) and url:
                    if url.startswith("//"):
                        url = "https:" + url
                    elif not url.startswith("http"):
                        url = "https://" + url
                    entry = {"website_url": url.rstrip("/")}
                    if self._row_photo:
                        photo = self._row_photo
                        if photo.startswith("//"):
                            photo = "https:" + photo
                        elif photo.startswith("/"):
                            photo = "https://www.house.gov" + photo
                        if photo.startswith(("https://", "http://")):
                            entry["photo_url"] = photo
                    self.entries[(self.state, district)] = entry
            self._in_row = False


def _state_from_name(value: str) -> str:
    names = {"Illinois": "IL"}
    return names.get(value)


def _replace_files(*files: tuple[Path, bytes]) -> None:
    temporary = []
    try:
        for path, content in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temp = tempfile.mkstemp(dir=path.parent, prefix=".house-")
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
            temporary.append((temp, path))
        for temp, path in temporary:
            os.replace(temp, path)
    finally:
        for temp, _ in temporary:
            Path(temp).unlink(missing_ok=True)
