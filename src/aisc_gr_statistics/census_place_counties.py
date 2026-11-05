"""2025 Census place-to-county relationships derived from GRF-C blocks."""

import csv
import hashlib
import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from io import BytesIO, TextIOWrapper
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import requests

REFERENCE_DIRECTORY = Path(__file__).resolve().parents[2] / "data/reference/census"
PLACES_PATH = REFERENCE_DIRECTORY / "illinois-places.json"
PLACE_COUNTIES_PATH = REFERENCE_DIRECTORY / "illinois-place-counties.json"
VALIDATION_PATH = REFERENCE_DIRECTORY / "illinois-place-counties-validation.json"
GRFC_SOURCE_URL = "https://www2.census.gov/geo/docs/maps-data/data/grf/2025/public_grfc_cur25_17.txt"
GAZETTEER_SOURCE_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_Gaz_place_national.zip"
VINTAGE = "2025"


class PlaceCountyError(ValueError):
    """A place-to-county snapshot could not be built or validated."""


def refresh_place_counties(
    places_path=PLACES_PATH,
    mapping_path=PLACE_COUNTIES_PATH,
    validation_path=VALIDATION_PATH,
    *,
    grfc_source_url=GRFC_SOURCE_URL,
    gazetteer_source_url=GAZETTEER_SOURCE_URL,
    get=requests.get,
    now=None,
):
    """Build place-to-county GEOID pairs and a validation report from 2025 sources."""
    places_path = Path(places_path)
    try:
        gazetteer_data = places_path.read_bytes()
        gazetteer_places = json.loads(gazetteer_data)
    except (OSError, json.JSONDecodeError) as error:
        raise PlaceCountyError("The checked-in Gazetteer Places snapshot is unreadable.") from error
    if not isinstance(gazetteer_places, dict):
        raise PlaceCountyError("The checked-in Gazetteer Places snapshot must be a GEOID-keyed object.")

    grfc_data = _download(grfc_source_url, get, zipped=False)
    pairs, invalid_codes, not_applicable = _parse_grfc(grfc_data)
    gazetteer_geoids = set(gazetteer_places)
    matched_pairs = sorted((place, county) for place, county in pairs if place in gazetteer_geoids)
    matched = {place for place, _ in matched_pairs}
    unmatched_places = sorted(gazetteer_geoids - matched)
    counts = defaultdict(set)
    for place, county in matched_pairs:
        counts[place].add(county)
    multi_county = {
        place: sorted(counties) for place, counties in sorted(counts.items()) if len(counties) > 1
    }
    mapping = defaultdict(list)
    for place, county in matched_pairs:
        mapping[place].append(county)
    mapping = {place: counties for place, counties in sorted(mapping.items())}

    retrieved_at = (now() if now else datetime.now(UTC)).astimezone(UTC)
    validation = {
        "vintage": VINTAGE,
        "grfc_source_url": grfc_source_url,
        "gazetteer_places_source_url": gazetteer_source_url,
        "gazetteer_places_snapshot": str(places_path.name),
        "retrieved_at": retrieved_at.isoformat().replace("+00:00", "Z"),
        "grfc_sha256": hashlib.sha256(grfc_data).hexdigest(),
        "gazetteer_places_sha256": hashlib.sha256(gazetteer_data).hexdigest(),
        "gazetteer_place_count": len(gazetteer_geoids),
        "matched_place_count": len(matched),
        "unmatched_place_geoids": unmatched_places,
        "invalid_code_records": invalid_codes,
        "not_applicable_place_records": not_applicable,
        "place_county_pair_count": len(matched_pairs),
        "multi_county_places": multi_county,
        "note": "Place membership does not identify the county of a particular address or facility; address-level geocoding is required for that use.",
    }
    _replace_json(Path(mapping_path), mapping)
    _replace_json(Path(validation_path), validation)
    return mapping, validation


def _download(url, get, *, zipped):
    try:
        response = get(url, timeout=60)
        response.raise_for_status()
        data = response.content
        if zipped:
            with ZipFile(BytesIO(data)) as archive:
                members = [name for name in archive.namelist() if name.lower().endswith(".txt")]
                if len(members) != 1:
                    raise PlaceCountyError("Census Gazetteer archive must contain one text file.")
                return archive.read(members[0])
        return data
    except (requests.RequestException, BadZipFile, OSError) as error:
        raise PlaceCountyError(f"Could not download the 2025 Census source: {error}") from error


def _parse_grfc(data):
    """Parse pipe-delimited GRF-C and return valid unique GEOID pairs and issues."""
    try:
        reader = csv.DictReader(TextIOWrapper(BytesIO(data), encoding="utf-8-sig"), delimiter="|")
        required = {"CURSTATE", "CURCOUNTY", "PLACEFP"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise PlaceCountyError("2025 GRF-C is missing CURSTATE, CURCOUNTY, or PLACEFP.")
        pairs = set()
        invalid = []
        not_applicable = 0
        for line_number, row in enumerate(reader, start=2):
            state, county, place = (str(row.get(key) or "").strip() for key in ("CURSTATE", "CURCOUNTY", "PLACEFP"))
            # 99999 is the GRF-C not-applicable fill for PLACEFP.
            if place == "99999":
                not_applicable += 1
                continue
            if not (re.fullmatch(r"\d{2}", state) and re.fullmatch(r"\d{3}", county)
                    and re.fullmatch(r"\d{5}", place)):
                invalid.append({"line": line_number, "CURSTATE": state, "CURCOUNTY": county, "PLACEFP": place})
                continue
            if state == "00" or county == "999" or place == "00000":
                invalid.append({"line": line_number, "CURSTATE": state, "CURCOUNTY": county, "PLACEFP": place})
                continue
            pairs.add((state + place, state + county))
        return pairs, invalid, not_applicable
    except UnicodeDecodeError as error:
        raise PlaceCountyError("2025 GRF-C is not valid UTF-8 text.") from error


def _replace_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
