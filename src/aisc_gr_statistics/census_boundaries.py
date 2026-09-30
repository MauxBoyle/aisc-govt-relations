"""Offline Census congressional-boundary snapshot support.

The renderer intentionally only reads this local KML file.  Refreshing it is
an explicit maintainer action, so making a PDF never needs internet access.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

import requests

BOUNDARY_DIRECTORY = Path(__file__).resolve().parents[2] / "data/reference/census"
KML_PATH = BOUNDARY_DIRECTORY / "illinois-congressional-districts.kml"
METADATA_PATH = BOUNDARY_DIRECTORY / "illinois-congressional-districts.json"
# Census cartographic boundary files are the published, government-owned source.
SOURCE_URL = "https://www2.census.gov/geo/tiger/GENZ2025/shp/cb_2025_us_cd119_500k.zip"
CONGRESSIONAL_SESSION = "119"


class BoundarySnapshotError(ValueError):
    """The local boundary snapshot is missing or cannot safely be rendered."""


def load_boundary_snapshot(kml_path=KML_PATH, metadata_path=METADATA_PATH):
    """Return ``{GEOID: points}`` after validating KML checksum and session."""
    kml_path, metadata_path = Path(kml_path), Path(metadata_path)
    if not kml_path.is_file() or not metadata_path.is_file():
        raise BoundarySnapshotError(
            "Census boundary snapshot is missing. Run `refresh-district-boundaries`."
        )
    try:
        data = kml_path.read_bytes()
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BoundarySnapshotError("Census boundary snapshot is unreadable.") from error
    required = {"source_url", "congressional_session", "retrieved_at", "kml_sha256"}
    if not isinstance(metadata, dict) or not required <= metadata.keys():
        raise BoundarySnapshotError("Census boundary metadata is incomplete.")
    if metadata["kml_sha256"] != hashlib.sha256(data).hexdigest():
        raise BoundarySnapshotError("Census boundary snapshot checksum does not match KML.")
    try:
        datetime.fromisoformat(str(metadata["retrieved_at"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise BoundarySnapshotError("Census boundary retrieval date is invalid.") from error
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise BoundarySnapshotError("Census boundary KML is malformed.") from error
    namespace = {"k": "http://www.opengis.net/kml/2.2"}
    shapes = {}
    for place in root.findall(".//k:Placemark", namespace):
        values = {item.get("name", ""): (item.findtext("k:value", namespaces=namespace) or "").strip() for item in place.findall(".//k:SimpleData", namespace)}
        geoid = values.get("GEOID") or (place.findtext("k:name", namespaces=namespace) or "").strip()
        coordinate_text = place.findtext(".//k:coordinates", namespaces=namespace) or ""
        points = []
        for item in coordinate_text.split():
            try:
                longitude, latitude, *_ = item.split(",")
                points.append((float(longitude), float(latitude)))
            except ValueError:
                continue
        if geoid and len(points) >= 3:
            shapes[geoid] = points
    if not shapes:
        raise BoundarySnapshotError("Census boundary KML contains no usable districts.")
    return shapes, metadata


def refresh_boundary_snapshot(kml_path=KML_PATH, metadata_path=METADATA_PATH, *, source_url=SOURCE_URL, congressional_session=CONGRESSIONAL_SESSION, get=requests.get, now=None):
    """Download a maintainer-supplied KML URL and record its provenance.

    Census publishes several formats; this command deliberately accepts a KML
    URL so a reviewed, Illinois-only KML can be selected before replacement.
    """
    try:
        response = get(source_url, timeout=30)
        response.raise_for_status()
    except requests.RequestException as error:
        raise BoundarySnapshotError(f"Could not download Census boundaries: {error}") from error
    data = response.content
    # Parse before writing, using a temporary metadata record solely for validation.
    kml_path = Path(kml_path)
    kml_path.parent.mkdir(parents=True, exist_ok=True)
    kml_path.write_bytes(data)
    metadata = {
        "source_url": source_url,
        "congressional_session": str(congressional_session),
        "retrieved_at": (now() if now else datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "kml_sha256": hashlib.sha256(data).hexdigest(),
    }
    Path(metadata_path).write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    load_boundary_snapshot(kml_path, metadata_path)
