"""Offline Census place and county points used by external district maps.

The district-enrichment CSV contains address-level geocoder coordinates for
audit purposes.  Those coordinates must never reach an external PDF.  This
module instead uses reviewed Census Gazetteer reference points for a saved
city, falling back to the saved county FIPS when the city is not a match.
"""

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import requests

REFERENCE_DIRECTORY = Path(__file__).resolve().parents[2] / "data/reference/census"
PLACES_PATH = REFERENCE_DIRECTORY / "illinois-places.json"
COUNTIES_PATH = REFERENCE_DIRECTORY / "illinois-counties.json"
METADATA_PATH = REFERENCE_DIRECTORY / "illinois-map-references.json"
# Gazetteer files include Census's published internal point for each geography.
PLACES_SOURCE_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_Gaz_place_national.zip"
COUNTIES_SOURCE_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_Gaz_counties_national.zip"


class MapReferenceError(ValueError):
    """A checked-in Census place or county reference is not safe to use."""


@dataclass(frozen=True)
class MapPoint:
    """A public, geography-level point; never an address-geocoder point."""

    longitude: float
    latitude: float

    def __iter__(self):
        return iter((self.longitude, self.latitude))

    def __getitem__(self, index):
        return (self.longitude, self.latitude)[index]


@dataclass(frozen=True)
class MapReferences:
    places: dict[str, MapPoint]
    counties: dict[str, MapPoint]
    metadata: dict


def normalize_city_name(value: str) -> str:
    """Return a conservative comparable city name, without guessing aliases."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold().replace("&", " and ")
    text = re.sub(r"\b(city|village|town|cdp|municipality)\b", " ", text)
    # Spaces and punctuation are not meaningful for Census-place matching:
    # this also makes saved "MC COOK" agree with Census's "McCook".
    return "".join(re.findall(r"[a-z0-9]+", text))


def load_map_references(
    places_path=PLACES_PATH, counties_path=COUNTIES_PATH, metadata_path=METADATA_PATH
) -> MapReferences:
    """Load and validate reviewed, version-controlled map reference data."""
    paths = tuple(Path(path) for path in (places_path, counties_path, metadata_path))
    if not all(path.is_file() for path in paths):
        raise MapReferenceError("Census map references are missing. Run `refresh-map-references`.")
    try:
        places_data = paths[0].read_bytes()
        counties_data = paths[1].read_bytes()
        metadata = json.loads(paths[2].read_text(encoding="utf-8"))
        places = json.loads(places_data)
        counties = json.loads(counties_data)
    except (OSError, json.JSONDecodeError) as error:
        raise MapReferenceError("Census map references are unreadable.") from error
    _validate_metadata(metadata, places_data, counties_data)
    return MapReferences(_points(places, "place"), _points(counties, "county"), metadata)


def point_for_company(city: str, county_fips: str, references: MapReferences) -> MapPoint | None:
    """Return a city point, or the public county point when city matching fails."""
    city_key = normalize_city_name(city)
    if city_key and city_key in references.places:
        return references.places[city_key]
    return references.counties.get(str(county_fips or "").strip().zfill(3))


def refresh_map_references(
    places_path=PLACES_PATH,
    counties_path=COUNTIES_PATH,
    metadata_path=METADATA_PATH,
    *,
    places_source_url=PLACES_SOURCE_URL,
    counties_source_url=COUNTIES_SOURCE_URL,
    get=requests.get,
    now=None,
):
    """Fetch official Gazetteer files and replace validated snapshots together."""
    place_rows = _download_rows(places_source_url, get)
    county_rows = _download_rows(counties_source_url, get)
    places = _gazetteer_points(place_rows, "place")
    counties = _gazetteer_points(county_rows, "county")
    if len(places) < 100 or len(counties) != 102:
        raise MapReferenceError("Census map references do not contain Illinois places and all 102 counties.")
    places_data = _json_bytes(places)
    counties_data = _json_bytes(counties)
    retrieved_at = (now() if now else datetime.now(UTC)).astimezone(UTC)
    metadata = {
        "places_source_url": places_source_url,
        "counties_source_url": counties_source_url,
        "retrieved_at": retrieved_at.isoformat().replace("+00:00", "Z"),
        "places_sha256": hashlib.sha256(places_data).hexdigest(),
        "counties_sha256": hashlib.sha256(counties_data).hexdigest(),
        "precision": "Census place internal point; county internal-point fallback",
    }
    _validate_metadata(metadata, places_data, counties_data)
    _replace_files(
        (Path(places_path), places_data), (Path(counties_path), counties_data),
        (Path(metadata_path), _json_bytes(metadata)),
    )
    return load_map_references(places_path, counties_path, metadata_path)


def _download_rows(url, get):
    from io import BytesIO
    from zipfile import BadZipFile, ZipFile

    try:
        response = get(url, timeout=30)
        response.raise_for_status()
        with ZipFile(BytesIO(response.content)) as archive:
            members = [member for member in archive.namelist() if member.lower().endswith(".txt")]
            if len(members) != 1:
                raise MapReferenceError("Census Gazetteer archive must contain exactly one text file.")
            return archive.read(members[0]).decode("utf-8-sig").splitlines()
    except (requests.RequestException, BadZipFile, OSError, UnicodeDecodeError) as error:
        raise MapReferenceError(f"Could not download Census map references: {error}") from error


def _gazetteer_points(lines: Iterable[str], kind):
    import csv

    # Recent Gazetteer releases use pipes; older releases used tab-delimited
    # files.  Supporting both makes the maintainer refresh command durable.
    reader = csv.DictReader(lines, delimiter="|" if lines and "|" in lines[0] else "\t")
    required = {"GEOID", "NAME", "INTPTLAT", "INTPTLONG"}
    if reader.fieldnames is None or not required <= set(reader.fieldnames):
        raise MapReferenceError("Census Gazetteer data is missing required columns.")
    points = {}
    for row in reader:
        geoid = (row.get("GEOID") or "").strip()
        if not geoid.startswith("17"):
            continue
        key = normalize_city_name(row["NAME"]) if kind == "place" else geoid[-3:]
        if not key or key in points:
            continue
        points[key] = {"name": row["NAME"].strip(), "longitude": _coordinate(row["INTPTLONG"]), "latitude": _coordinate(row["INTPTLAT"])}
    return points


def _coordinate(value):
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise MapReferenceError("Census map references contain invalid coordinates.") from error
    if not (-180 <= result <= 180):
        raise MapReferenceError("Census map references contain invalid coordinates.")
    return result


def _points(values, kind):
    if not isinstance(values, dict) or not values:
        raise MapReferenceError("Census map references are incomplete.")
    result = {}
    for key, value in values.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            raise MapReferenceError("Census map references are malformed.")
        longitude, latitude = _coordinate(value.get("longitude")), _coordinate(value.get("latitude"))
        if not -90 <= latitude <= 90:
            raise MapReferenceError("Census map references contain invalid coordinates.")
        result[key] = MapPoint(longitude, latitude)
    if kind == "county" and len(result) != 102:
        raise MapReferenceError("Census county map references must contain all 102 Illinois counties.")
    return result


def _validate_metadata(metadata, places_data, counties_data):
    required = {"places_source_url", "counties_source_url", "retrieved_at", "places_sha256", "counties_sha256", "precision"}
    if not isinstance(metadata, dict) or not required <= metadata.keys():
        raise MapReferenceError("Census map-reference metadata is incomplete.")
    if metadata["places_sha256"] != hashlib.sha256(places_data).hexdigest() or metadata["counties_sha256"] != hashlib.sha256(counties_data).hexdigest():
        raise MapReferenceError("Census map-reference checksum does not match snapshot data.")
    try:
        datetime.fromisoformat(str(metadata["retrieved_at"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise MapReferenceError("Census map-reference retrieval date is invalid.") from error


def _json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _replace_files(*files):
    """Write only after all validation; simple local replacement is sufficient here."""
    staged = []
    try:
        for path, content in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_bytes(content)
            staged.append((temporary, path))
        for temporary, path in staged:
            temporary.replace(path)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
