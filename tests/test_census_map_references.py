"""Tests for privacy-safe Census map-marker reference data."""

import hashlib
import json

import pytest

from aisc_gr_statistics.census_map_references import (
    MapPoint,
    MapReferenceError,
    MapReferences,
    load_map_references,
    normalize_city_name,
    point_for_company,
)
from aisc_gr_statistics.external_district_report import (
    _cluster_markers,
    _map_extent,
    _project_point,
)


def test_city_matching_normalizes_names_and_falls_back_to_county():
    references = MapReferences(
        {"mccook": MapPoint(-87.8, 41.7)},
        {"031": MapPoint(-87.7, 41.8)},
        {},
    )

    assert normalize_city_name("Mc Cook village") == "mccook"
    assert point_for_company("McCook", "031", references) == MapPoint(-87.8, 41.7)
    assert point_for_company("Unmatched City", "31", references) == MapPoint(-87.7, 41.8)
    assert point_for_company("Unmatched City", "", references) is None


def test_reference_loader_rejects_changed_snapshot_data(tmp_path):
    places, counties, metadata = (tmp_path / name for name in ("places.json", "counties.json", "metadata.json"))
    places.write_text('{"chicago": {"longitude": -87.6, "latitude": 41.8}}\n', encoding="utf-8")
    counties.write_text("{}\n", encoding="utf-8")
    metadata.write_text(json.dumps({
        "places_source_url": "https://census.example/places.zip",
        "counties_source_url": "https://census.example/counties.zip",
        "retrieved_at": "2026-01-01T00:00:00Z",
        "places_sha256": hashlib.sha256(places.read_bytes()).hexdigest(),
        "counties_sha256": "changed",
        "precision": "Census place internal point; county internal-point fallback",
    }), encoding="utf-8")

    with pytest.raises(MapReferenceError, match="checksum"):
        load_map_references(places, counties, metadata)


def test_house_extent_includes_marker_and_projection_fits_printable_map():
    shapes = {"1701": [[(-89, 40), (-88, 40), (-88, 41), (-89, 40)]]}
    extent = _map_extent(shapes, "1701", [MapPoint(-87, 42)], 180 / 105)
    projected = _project_point(MapPoint(-87, 42), extent, 0, 0, 180, 105)

    assert extent[1] >= -87
    assert 0 <= projected[0] <= 180 and 0 <= projected[1] <= 105


def test_marker_clustering_uses_final_pdf_positions_deterministically():
    assert _cluster_markers([(20, 20), (10, 10), (14, 10), (100, 100)]) == [
        [(10, 10), (14, 10)], [(20, 20)], [(100, 100)]
    ]
