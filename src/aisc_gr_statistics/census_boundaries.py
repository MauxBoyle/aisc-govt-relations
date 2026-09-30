"""Offline Census congressional-boundary snapshot support.

The renderer reads only the checked-in KML. Refreshing downloads Census's
Illinois KML archive, validates it entirely in memory, and only then replaces
the local reference files.
"""

import hashlib
import json
import math
import os
import tempfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

import requests

BOUNDARY_DIRECTORY = Path(__file__).resolve().parents[2] / "data/reference/census"
KML_PATH = BOUNDARY_DIRECTORY / "illinois-congressional-districts.kml"
METADATA_PATH = BOUNDARY_DIRECTORY / "illinois-congressional-districts.json"
# This Illinois-only KML ZIP is Census's 2025 cartographic boundary file.
SOURCE_URL = "https://www2.census.gov/geo/tiger/GENZ2025/kml/cb_2025_17_cd119_500k.zip"
CONGRESSIONAL_SESSION = "119"
ILLINOIS_GEOIDS = frozenset(f"17{district:02d}" for district in range(1, 18))
KML_NAMESPACE = {"k": "http://www.opengis.net/kml/2.2"}


class BoundarySnapshotError(ValueError):
    """The local boundary snapshot is missing or cannot safely be rendered."""


def load_boundary_snapshot(kml_path=KML_PATH, metadata_path=METADATA_PATH):
    """Return Illinois GEOIDs mapped to every outer polygon ring they contain."""
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
    _validate_metadata(metadata, data)
    return _parse_illinois_kml(data, metadata["congressional_session"]), metadata


def refresh_boundary_snapshot(
    kml_path=KML_PATH,
    metadata_path=METADATA_PATH,
    *,
    source_url=SOURCE_URL,
    congressional_session=CONGRESSIONAL_SESSION,
    get=requests.get,
    now=None,
):
    """Download, validate, then replace the local Census KML snapshot.

    ``source_url`` and ``congressional_session`` remain optional overrides for
    maintainers testing a future official Census release.
    """
    try:
        response = get(source_url, timeout=30)
        response.raise_for_status()
    except requests.RequestException as error:
        raise BoundarySnapshotError(f"Could not download Census boundaries: {error}") from error

    kml_data = _extract_kml(response.content)
    # Parsing and state/district validation happen before any tracked file changes.
    _parse_illinois_kml(kml_data, str(congressional_session))
    retrieved_at = (now() if now else datetime.now(UTC)).astimezone(UTC)
    metadata = {
        "source_url": source_url,
        "congressional_session": str(congressional_session),
        "retrieved_at": retrieved_at.isoformat().replace("+00:00", "Z"),
        "kml_sha256": hashlib.sha256(kml_data).hexdigest(),
    }
    _validate_metadata(metadata, kml_data)
    _replace_snapshot_files(Path(kml_path), Path(metadata_path), kml_data, metadata)
    return load_boundary_snapshot(kml_path, metadata_path)


def _extract_kml(archive_data):
    """Read the one KML file from a Census ZIP without extracting to disk."""
    try:
        with ZipFile(BytesIO(archive_data)) as archive:
            kml_members = [
                info
                for info in archive.infolist()
                if not info.is_dir() and info.filename.lower().endswith(".kml")
            ]
            if len(kml_members) != 1:
                raise BoundarySnapshotError("Census archive must contain exactly one KML file.")
            return archive.read(kml_members[0])
    except (BadZipFile, OSError) as error:
        raise BoundarySnapshotError("Census download is not a valid KML ZIP archive.") from error


def _parse_illinois_kml(data, congressional_session=CONGRESSIONAL_SESSION):
    """Parse KML and retain every outer ring, including multipart districts."""
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise BoundarySnapshotError("Census boundary KML is malformed.") from error

    shapes = {}
    for place in root.findall(".//k:Placemark", KML_NAMESPACE):
        values = {
            item.get("name", ""): (item.text or "").strip()
            for item in place.findall(".//k:SimpleData", KML_NAMESPACE)
        }
        geoid = values.get("GEOID") or (
            place.findtext("k:name", namespaces=KML_NAMESPACE) or ""
        ).strip()
        coordinate_elements = place.findall(
            ".//k:outerBoundaryIs/k:LinearRing/k:coordinates", KML_NAMESPACE
        )
        if (
            geoid
            and values.get("STATEFP") == "17"
            and values.get("CDSESSN") == str(congressional_session)
        ):
            rings = [_coordinates(element.text or "") for element in coordinate_elements]
            if not rings:
                continue
            if geoid in shapes:
                raise BoundarySnapshotError(
                    "Census boundary KML contains a duplicate district GEOID."
                )
            shapes.setdefault(geoid, []).extend(rings)
    if set(shapes) != ILLINOIS_GEOIDS:
        raise BoundarySnapshotError(
            "Census boundary KML must contain exactly Illinois districts 1701 through 1717."
        )
    return shapes


def _coordinates(text):
    points = []
    for item in text.split():
        try:
            longitude, latitude, *_ = item.split(",")
            point = float(longitude), float(latitude)
        except ValueError as error:
            raise BoundarySnapshotError("Census boundary geometry contains invalid coordinates.") from error
        within_world_bounds = -180 <= point[0] <= 180 and -90 <= point[1] <= 90
        if not all(math.isfinite(value) for value in point) or not within_world_bounds:
            raise BoundarySnapshotError("Census boundary geometry contains invalid coordinates.")
        points.append(point)
    if len(points) < 4 or points[0] != points[-1]:
        raise BoundarySnapshotError("Census boundary geometry must contain closed polygon rings.")
    return points


def _validate_metadata(metadata, kml_data):
    required = {"source_url", "congressional_session", "retrieved_at", "kml_sha256"}
    if not isinstance(metadata, dict) or not required <= metadata.keys():
        raise BoundarySnapshotError("Census boundary metadata is incomplete.")
    if metadata["kml_sha256"] != hashlib.sha256(kml_data).hexdigest():
        raise BoundarySnapshotError("Census boundary snapshot checksum does not match KML.")
    try:
        datetime.fromisoformat(str(metadata["retrieved_at"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise BoundarySnapshotError("Census boundary retrieval date is invalid.") from error


def _replace_snapshot_files(kml_path, metadata_path, kml_data, metadata):
    """Write validated files beside their targets, then replace each atomically."""
    kml_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_paths = []
    try:
        for target, content in (
            (kml_path, kml_data),
            (metadata_path, (json.dumps(metadata, indent=2) + "\n").encode()),
        ):
            descriptor, temporary_name = tempfile.mkstemp(
                dir=target.parent, prefix=f".{target.name}."
            )
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(content)
            temporary_paths.append((Path(temporary_name), target))
        for temporary, target in temporary_paths:
            os.replace(temporary, target)
    finally:
        for temporary, _ in temporary_paths:
            temporary.unlink(missing_ok=True)
